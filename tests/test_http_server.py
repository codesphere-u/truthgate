"""Comprehensive tests for TruthGate HTTP server, Streamable MCP, Workbench UI, and security hardening."""

from __future__ import annotations

import http.client
import io
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from truthgate.benchmark import _populate_benchmark_workspace
from truthgate.cli import build_parser
from truthgate.cli import main as cli_main
from truthgate.http_server import (
    TruthGateHTTPApp,
    TruthGateThreadingHTTPServer,
    create_http_server,
)
from truthgate.mcp_server import TruthGateMCPServer


@pytest.fixture
def live_server() -> Iterator[tuple[str, int, TruthGateThreadingHTTPServer]]:
    server = create_http_server(host="127.0.0.1", port=0, max_body_bytes=4096)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield host, port, server
    finally:
        server.shutdown()
        server.server_close()


def _http_call(
    host: str,
    port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        raw = resp.read()
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, hdrs, raw
    finally:
        conn.close()


def _assert_security_headers(headers: dict[str, str], *, is_api: bool = False) -> None:
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "SAMEORIGIN"
    assert "referrer-policy" in headers
    assert headers["access-control-allow-origin"] == "*"
    assert headers.get("x-request-id")
    if is_api:
        assert headers.get("cache-control") == "no-store"


def test_root_html_workbench_and_json_content_negotiation(
    live_server: tuple[str, int, TruthGateThreadingHTTPServer],
) -> None:
    host, port, _ = live_server

    # HTML Workbench when Accept: text/html
    status, headers, body = _http_call(
        host,
        port,
        "GET",
        "/",
        headers={"Accept": "text/html,application/xhtml+xml"},
    )
    assert status == 200
    assert "text/html" in headers["content-type"]
    _assert_security_headers(headers)
    html = body.decode("utf-8")
    assert "TruthGate Developer Workbench" in html
    assert "/api/verify" in html
    assert "/api/benchmark" in html

    # JSON info when Accept is application/json or */*
    status_j, headers_j, body_j = _http_call(
        host,
        port,
        "GET",
        "/",
        headers={"Accept": "application/json"},
    )
    assert status_j == 200
    assert "application/json" in headers_j["content-type"]
    _assert_security_headers(headers_j)
    info = json.loads(body_j)
    assert info["service"] == "truthgate"
    assert "truthgate_verify_response" in info["mcp"]["tools"]
    assert "truthgate://rules" in info["mcp"]["resources"]
    assert "audit-response" in info["mcp"]["prompts"]

    # HEAD / returns empty body with matching headers
    status_h, headers_h, body_h = _http_call(
        host,
        port,
        "HEAD",
        "/",
        headers={"Accept": "text/html"},
    )
    assert status_h == 200
    assert body_h == b""
    assert "text/html" in headers_h["content-type"]


def test_health_ready_info_and_metrics_get_and_head(
    live_server: tuple[str, int, TruthGateThreadingHTTPServer],
) -> None:
    host, port, _ = live_server

    for path in ("/health", "/healthz", "/readyz", "/info"):
        st_get, hdr_get, body_get = _http_call(host, port, "GET", path)
        assert st_get == 200
        _assert_security_headers(hdr_get)
        parsed = json.loads(body_get)
        assert parsed["service"] == "truthgate"

        st_head, hdr_head, body_head = _http_call(host, port, "HEAD", path)
        assert st_head == 200
        assert body_head == b""
        _assert_security_headers(hdr_head)

    # Metrics Prometheus text format
    st_prom, hdr_prom, body_prom = _http_call(host, port, "GET", "/metrics")
    assert st_prom == 200
    assert "text/plain" in hdr_prom["content-type"]
    prom_text = body_prom.decode("utf-8")
    assert "truthgate_http_requests_total" in prom_text
    assert "truthgate_audits_total" in prom_text

    # Metrics JSON format via Accept and via ?format=json
    st_mj, hdr_mj, body_mj = _http_call(
        host, port, "GET", "/metrics", headers={"Accept": "application/json"}
    )
    assert st_mj == 200
    assert "application/json" in hdr_mj["content-type"]
    assert json.loads(body_mj)["requests_total"] >= 1

    st_mq, _, body_mq = _http_call(host, port, "GET", "/metrics?format=json")
    assert st_mq == 200
    assert "uptime_seconds" in json.loads(body_mq)


def test_request_id_echo_sanitization_and_cors_options(
    live_server: tuple[str, int, TruthGateThreadingHTTPServer],
) -> None:
    host, port, _ = live_server

    # Valid X-Request-ID is echoed verbatim
    st, hdrs, _ = _http_call(
        host,
        port,
        "GET",
        "/health",
        headers={"X-Request-ID": "trace-abc_123.9:1"},
    )
    assert st == 200
    assert hdrs["x-request-id"] == "trace-abc_123.9:1"

    # Malicious / unsafe X-Request-ID is sanitized and never reflected verbatim
    evil_id = "<script>alert(1)</script>;drop"
    st_evil, hdrs_evil, _ = _http_call(
        host,
        port,
        "GET",
        "/health",
        headers={"X-Request-ID": evil_id},
    )
    assert st_evil == 200
    safe_id = hdrs_evil["x-request-id"]
    assert safe_id != evil_id
    assert "<" not in safe_id
    assert ">" not in safe_id

    # Oversized X-Request-ID is replaced
    st_long, hdrs_long, _ = _http_call(
        host,
        port,
        "GET",
        "/health",
        headers={"X-Request-ID": "a" * 300},
    )
    assert st_long == 200
    assert len(hdrs_long["x-request-id"]) <= 128

    # OPTIONS CORS preflight
    st_opt, hdrs_opt, body_opt = _http_call(host, port, "OPTIONS", "/api/verify")
    assert st_opt == 204
    assert body_opt == b""
    assert hdrs_opt["access-control-allow-origin"] == "*"
    assert "POST" in hdrs_opt["access-control-allow-methods"]
    assert "X-Request-ID" in hdrs_opt["access-control-allow-headers"]
    _assert_security_headers(hdrs_opt, is_api=True)


def test_mcp_http_and_sse_endpoints_and_new_capabilities(
    live_server: tuple[str, int, TruthGateThreadingHTTPServer],
) -> None:
    host, port, _ = live_server

    # GET /mcp without text/event-stream -> 405 Allow: POST, OPTIONS
    st_405, hdrs_405, body_405 = _http_call(
        host, port, "GET", "/mcp", headers={"Accept": "application/json"}
    )
    assert st_405 == 405
    assert hdrs_405["allow"] == "POST, OPTIONS"
    _assert_security_headers(hdrs_405, is_api=True)
    assert json.loads(body_405)["error"] == "Method Not Allowed"

    # GET /mcp with text/event-stream -> 200 SSE
    st_sse, hdrs_sse, body_sse = _http_call(
        host, port, "GET", "/mcp", headers={"Accept": "text/event-stream"}
    )
    assert st_sse == 200
    assert "text/event-stream" in hdrs_sse["content-type"]
    assert hdrs_sse.get("mcp-session-id")
    sse_text = body_sse.decode("utf-8")
    assert "event: endpoint" in sse_text
    assert "/messages?sessionId=" in sse_text

    # POST /mcp initialize -> advertises tools, resources, prompts
    st_init, hdrs_init, body_init = _http_call(
        host,
        port,
        "POST",
        "/mcp",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}).encode("utf-8"),
    )
    assert st_init == 200
    session_id = hdrs_init["mcp-session-id"]
    init_res = json.loads(body_init)["result"]
    assert "tools" in init_res["capabilities"]
    assert "resources" in init_res["capabilities"]
    assert "prompts" in init_res["capabilities"]

    # Notification returns 202 Accepted with empty body
    st_notif, _, body_notif = _http_call(
        host,
        port,
        "POST",
        "/mcp",
        headers={"Content-Type": "application/json", "Mcp-Session-Id": session_id},
        body=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode("utf-8"),
    )
    assert st_notif == 202
    assert body_notif == b""

    # tools/list, resources/list, resources/read, prompts/list, prompts/get
    for method, expected_key in (
        ("tools/list", "tools"),
        ("resources/list", "resources"),
        ("prompts/list", "prompts"),
    ):
        st, hdrs, raw = _http_call(
            host,
            port,
            "POST",
            "/mcp",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"jsonrpc": "2.0", "id": 2, "method": method}).encode("utf-8"),
        )
        assert st == 200
        _assert_security_headers(hdrs, is_api=True)
        assert expected_key in json.loads(raw)["result"]

    # resources/read over /messages
    for uri in (
        "truthgate://rules",
        "truthgate://benchmark/scenarios",
        "truthgate://docs/architecture",
    ):
        st, _, raw = _http_call(
            host,
            port,
            "POST",
            "/messages",
            headers={"Content-Type": "application/json"},
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "resources/read",
                    "params": {"uri": uri},
                }
            ).encode("utf-8"),
        )
        assert st == 200
        contents = json.loads(raw)["result"]["contents"]
        assert len(contents) == 1
        assert contents[0]["uri"] == uri
        assert len(contents[0]["text"]) > 20

    # Unknown resource -> -32002
    st_unk_res, _, raw_unk_res = _http_call(
        host,
        port,
        "POST",
        "/mcp",
        headers={"Content-Type": "application/json"},
        body=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "resources/read",
                "params": {"uri": "truthgate://does-not-exist"},
            }
        ).encode("utf-8"),
    )
    assert st_unk_res == 200
    assert json.loads(raw_unk_res)["error"]["code"] == -32002

    # prompts/get for all 3 prompts
    for p_name, p_args in (
        ("audit-response", {"response_text": "TLS 1.0 is permitted [sec_policy]."}),
        ("ground-workspace", {"response_text": "See `src/auth/jwt.py`", "workspace_root": "."}),
        ("surgical-repair", {"violation_codes": "TG003", "offending_claim": "TLS 1.0 permitted"}),
    ):
        st, _, raw = _http_call(
            host,
            port,
            "POST",
            "/mcp",
            headers={"Content-Type": "application/json"},
            body=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 12,
                    "method": "prompts/get",
                    "params": {"name": p_name, "arguments": p_args},
                }
            ).encode("utf-8"),
        )
        assert st == 200
        res = json.loads(raw)["result"]
        assert len(res["messages"]) == 1
        assert res["messages"][0]["role"] == "user"

    # POST /mcp with Accept: text/event-stream emits SSE message frame
    st_psse, hdr_psse, body_psse = _http_call(
        host,
        port,
        "POST",
        "/mcp",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        body=json.dumps({"jsonrpc": "2.0", "id": 99, "method": "ping"}).encode("utf-8"),
    )
    assert st_psse == 200
    assert "text/event-stream" in hdr_psse["content-type"]
    assert "event: message" in body_psse.decode("utf-8")

    # Malformed JSON -> -32700
    st_bad, _, body_bad = _http_call(
        host,
        port,
        "POST",
        "/mcp",
        headers={"Content-Type": "application/json"},
        body=b"{bad json",
    )
    assert st_bad == 400
    assert json.loads(body_bad)["error"]["code"] == -32700

    # Batch JSON-RPC request
    st_batch, _, body_batch = _http_call(
        host,
        port,
        "POST",
        "/mcp",
        headers={"Content-Type": "application/json"},
        body=json.dumps(
            [
                {"jsonrpc": "2.0", "id": 21, "method": "ping"},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 22, "method": "tools/list"},
            ]
        ).encode("utf-8"),
    )
    assert st_batch == 200
    batch_items = json.loads(body_batch)
    assert len(batch_items) == 2


def test_rest_api_endpoints_and_validation(
    live_server: tuple[str, int, TruthGateThreadingHTTPServer],
    tmp_path: Path,
) -> None:
    host, port, _ = live_server
    _populate_benchmark_workspace(tmp_path)

    # 1. GET /api/rules
    st, hdrs, body = _http_call(host, port, "GET", "/api/rules")
    assert st == 200
    _assert_security_headers(hdrs, is_api=True)
    assert json.loads(body)["total_rules"] == 8

    # 2. POST /api/verify
    verify_req = {
        "response_text": (
            "TLS 1.0 is permitted in production [sec_policy]. "
            "See `src/auth/jwt.py` and `verify_token_v2()`."
        ),
        "sources": [
            {
                "source_id": "sec_policy",
                "title": "TLS Policy",
                "content": "TLS 1.0 is not permitted in production.",
            }
        ],
        "workspace_root": str(tmp_path),
    }
    st_v, hdr_v, body_v = _http_call(
        host,
        port,
        "POST",
        "/api/verify",
        headers={"Content-Type": "application/json"},
        body=json.dumps(verify_req).encode("utf-8"),
    )
    assert st_v == 200
    _assert_security_headers(hdr_v, is_api=True)
    v_data = json.loads(body_v)
    assert v_data["passed"] is False
    assert "TG003" in v_data["violation_counts"]
    assert "TG008" in v_data["violation_counts"]
    assert "repair_prompt" in v_data

    # 3. POST /api/ground
    st_g, _, body_g = _http_call(
        host,
        port,
        "POST",
        "/api/ground",
        headers={"Content-Type": "application/json"},
        body=json.dumps(
            {
                "response_text": "See `src/auth/jwt.py#L8-L19` via `TokenValidator.verify_token`.",
                "workspace_root": str(tmp_path),
            }
        ).encode("utf-8"),
    )
    assert st_g == 200
    assert json.loads(body_g)["passed"] is True

    # 4. POST /api/decompose
    st_d, _, body_d = _http_call(
        host,
        port,
        "POST",
        "/api/decompose",
        headers={"Content-Type": "application/json"},
        body=json.dumps(
            {"response_text": "Latency is 42.8 ms [sla]. Availability is 99.95% [sla]."}
        ).encode("utf-8"),
    )
    assert st_d == 200
    assert json.loads(body_d)["total_claims"] == 2

    # 5. GET /api/demo & POST /api/benchmark
    st_demo, _, body_demo = _http_call(host, port, "GET", "/api/demo")
    assert st_demo == 200
    assert json.loads(body_demo)["result"]["passed"] is False

    st_bench, _, body_bench = _http_call(host, port, "POST", "/api/benchmark")
    assert st_bench == 200
    bench_data = json.loads(body_bench)
    assert bench_data["total_scenarios"] == 40
    assert bench_data["precision"] == 1.0

    # 6. Validation errors (400), 404, and 405
    st_bad, _, _ = _http_call(
        host,
        port,
        "POST",
        "/api/verify",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"response_text": 123}).encode("utf-8"),
    )
    assert st_bad == 400

    st_404, hdr_404, _ = _http_call(host, port, "GET", "/api/does-not-exist")
    assert st_404 == 404
    _assert_security_headers(hdr_404, is_api=True)

    st_405, hdr_405, _ = _http_call(host, port, "PUT", "/health")
    assert st_405 == 405
    assert "GET" in hdr_405["allow"]


def test_body_size_limit_413_and_no_traceback_leaks(
    live_server: tuple[str, int, TruthGateThreadingHTTPServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, port, server = live_server

    # 1. Oversized body (> 4096 bytes) returns 413
    oversized = json.dumps({"response_text": "x" * 5000}).encode("utf-8")
    st_413, hdr_413, body_413 = _http_call(
        host,
        port,
        "POST",
        "/api/verify",
        headers={"Content-Type": "application/json"},
        body=oversized,
    )
    assert st_413 == 413
    _assert_security_headers(hdr_413, is_api=True)
    assert json.loads(body_413)["code"] == 413

    # 2. Invalid Content-Length returns 400
    st_cl, _, _ = _http_call(
        host,
        port,
        "POST",
        "/api/verify",
        headers={"Content-Type": "application/json", "Content-Length": "-5"},
        body=b"",
    )
    assert st_cl == 400

    # 3. Internal exception does not leak traceback or sensitive internal message
    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("sensitive_internal_secret_token_999")

    monkeypatch.setattr(server.app, "_handle_api_benchmark", boom)
    server.app._route_table["/api/benchmark"]["GET"] = boom
    st_500, hdr_500, body_500 = _http_call(host, port, "GET", "/api/benchmark")
    assert st_500 == 500
    _assert_security_headers(hdr_500, is_api=True)
    text_500 = body_500.decode("utf-8")
    assert "Traceback" not in text_500
    assert "sensitive_internal_secret_token_999" not in text_500
    assert json.loads(text_500)["error"] == "Internal server error"


def test_http_app_direct_edge_cases() -> None:
    app = TruthGateHTTPApp(max_body_bytes=128)
    with pytest.raises(ValueError, match="max_body_bytes"):
        TruthGateHTTPApp(max_body_bytes=0)

    # Oversized body directly through handle_http
    resp_413 = app.handle_http("POST", "/mcp", {}, b"x" * 200)
    assert resp_413.status == 413

    # Non-dict JSON-RPC payload
    resp_invalid = app.handle_http("POST", "/mcp", {}, b"123")
    assert resp_invalid.status == 400
    assert json.loads(resp_invalid.body)["error"]["code"] == -32600

    # Empty JSON-RPC batch
    resp_empty_batch = app.handle_http("POST", "/mcp", {}, b"[]")
    assert resp_empty_batch.status == 400
    assert json.loads(resp_empty_batch.body)["error"]["code"] == -32600


def test_cli_serve_http_parser_and_stdio_mcp_edge_cases(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = build_parser()
    parsed = parser.parse_args(["serve-http", "--host", "0.0.0.0", "--port", "9090"])
    assert parsed.command == "serve-http"
    assert parsed.host == "0.0.0.0"
    assert parsed.port == 9090

    called: dict[str, Any] = {}

    def fake_serve_http(*, host: str, port: int, max_body_bytes: int) -> None:
        called["host"] = host
        called["port"] = port
        called["max_body_bytes"] = max_body_bytes

    monkeypatch.setattr("truthgate.cli.serve_http", fake_serve_http)
    assert cli_main(["serve-http", "--host", "127.0.0.1", "--port", "8123"]) == 0
    assert called == {"host": "127.0.0.1", "port": 8123, "max_body_bytes": 1_048_576}

    # Exercise stdio MCP server loop with valid, notification, and malformed lines
    mcp = TruthGateMCPServer()
    stdin_buf = io.StringIO(
        "\n"
        '{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
        '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        "{malformed\n"
    )
    stdout_buf = io.StringIO()
    mcp.serve_stdio(stdin=stdin_buf, stdout=stdout_buf)
    lines = [json.loads(line) for line in stdout_buf.getvalue().splitlines()]
    assert len(lines) == 2
    assert lines[0]["result"] == {}
    assert lines[1]["error"]["code"] == -32700

    # Exercise human-readable CLI paths for coverage
    assert cli_main(["demo"]) == 0
    assert "TRUTHGATE" in capsys.readouterr().out
    assert cli_main(["rules"]) == 0
    assert "TG001" in capsys.readouterr().out
    assert cli_main(["benchmark"]) == 0
    assert "Precision" in capsys.readouterr().out
