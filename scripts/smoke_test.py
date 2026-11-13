"""End-to-end live HTTP & MCP smoke test script for TruthGate."""

from __future__ import annotations

import http.client
import json
import sys
import threading

from truthgate.http_server import create_http_server


def _request(
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
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, resp_headers, raw
    finally:
        conn.close()


def run_smoke_test() -> None:
    """Boot TruthGate HTTP server on an ephemeral port and verify core contracts."""
    server = create_http_server(host="127.0.0.1", port=0, max_body_bytes=4096)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        # 1. GET / (HTML & JSON negotiation)
        st, hdrs, raw = _request(host, port, "GET", "/", headers={"Accept": "text/html"})
        assert st == 200 and "text/html" in hdrs["content-type"]
        assert b"TruthGate Developer Workbench" in raw

        st, hdrs, raw = _request(host, port, "GET", "/", headers={"Accept": "application/json"})
        assert st == 200 and json.loads(raw)["service"] == "truthgate"
        assert hdrs["x-content-type-options"] == "nosniff"
        assert hdrs["x-frame-options"] == "SAMEORIGIN"

        # 2. GET & HEAD health/info/metrics
        for endpoint in ("/health", "/healthz", "/readyz", "/info"):
            st_g, _, _ = _request(host, port, "GET", endpoint)
            st_h, _, raw_h = _request(host, port, "HEAD", endpoint)
            assert st_g == 200 and st_h == 200 and raw_h == b""

        st, hdrs, raw = _request(host, port, "GET", "/metrics")
        assert st == 200 and "truthgate_http_requests_total" in raw.decode("utf-8")

        # 3. X-Request-ID echo & sanitization
        st, hdrs, _ = _request(host, port, "GET", "/health", headers={"X-Request-ID": "smoke-123"})
        assert st == 200 and hdrs["x-request-id"] == "smoke-123"

        # 4. GET /mcp -> 405 without SSE, 200 with SSE
        st, hdrs, _ = _request(host, port, "GET", "/mcp")
        assert st == 405 and hdrs["allow"] == "POST, OPTIONS"

        st, hdrs, raw = _request(host, port, "GET", "/mcp", headers={"Accept": "text/event-stream"})
        assert st == 200 and "text/event-stream" in hdrs["content-type"]

        # 5. POST /mcp malformed JSON -> -32700, valid initialize + tools/list + resources/list + prompts/list
        st, _, raw = _request(
            host,
            port,
            "POST",
            "/mcp",
            headers={"Content-Type": "application/json"},
            body=b"{not-json",
        )
        assert st == 400 and json.loads(raw)["error"]["code"] == -32700

        for req_id, method in enumerate(
            ("initialize", "tools/list", "resources/list", "prompts/list"), start=1
        ):
            payload = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method}).encode("utf-8")
            st, _, raw = _request(
                host,
                port,
                "POST",
                "/mcp",
                headers={"Content-Type": "application/json"},
                body=payload,
            )
            assert st == 200 and "result" in json.loads(raw)

        # 6. REST /api/verify & 413 body limit
        verify_body = json.dumps(
            {
                "response_text": "TLS 1.0 is permitted in production [sec_policy].",
                "sources": [
                    {
                        "source_id": "sec_policy",
                        "content": "TLS 1.0 is not permitted in production.",
                    }
                ],
            }
        ).encode("utf-8")
        st, hdrs, raw = _request(
            host,
            port,
            "POST",
            "/api/verify",
            headers={"Content-Type": "application/json"},
            body=verify_body,
        )
        assert st == 200
        assert hdrs.get("cache-control") == "no-store"
        assert "TG003" in json.loads(raw)["violation_counts"]

        st, _, _ = _request(
            host,
            port,
            "POST",
            "/api/verify",
            headers={"Content-Type": "application/json"},
            body=b"x" * 5000,
        )
        assert st == 413

        print(f"TruthGate smoke test PASSED against http://{host}:{port}")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    try:
        run_smoke_test()
    except AssertionError as exc:
        print(f"Smoke test FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
