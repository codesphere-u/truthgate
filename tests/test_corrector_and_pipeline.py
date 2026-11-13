"""Comprehensive tests for SurgicalRepairCompiler, 40-scenario benchmark, CLI, and MCP Server."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from truthgate.benchmark import (
    BenchmarkScenario,
    _build_benchmark_sources,
    _populate_benchmark_workspace,
    get_benchmark_scenarios,
    run_benchmark_suite,
)
from truthgate.cli import main as cli_main
from truthgate.corrector import TruthGateVerifier
from truthgate.mcp_server import TruthGateMCPServer

SCENARIOS = get_benchmark_scenarios()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.scenario_id for s in SCENARIOS])
def test_each_benchmark_scenario_deterministically(
    scenario: BenchmarkScenario, tmp_path: Path
) -> None:
    sources = _build_benchmark_sources()
    _populate_benchmark_workspace(tmp_path)
    verifier = TruthGateVerifier()
    result = verifier.verify(
        response_text=scenario.response_text,
        sources=sources,
        workspace_root=tmp_path if scenario.use_workspace else None,
    )
    assert result.passed is scenario.expected_passed
    if not scenario.expected_passed:
        assert scenario.expected_rule in result.violation_counts
        assert scenario.expected_rule in result.repair_prompt
    else:
        assert result.repair_prompt == ""


def test_benchmark_suite_aggregate_metrics() -> None:
    summary = run_benchmark_suite()
    assert summary["total_scenarios"] == 40
    assert summary["grounded_scenarios"] == 10
    assert summary["adversarial_scenarios"] == 30
    assert summary["false_positives"] == 0
    assert summary["false_negatives"] == 0
    assert summary["precision"] == 1.0
    assert summary["recall"] == 1.0
    assert summary["false_positive_rate"] == 0.0
    assert summary["exact_rule_accuracy"] == 1.0
    assert summary["p50_latency_ms"] < 1.0


def test_cli_commands_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 1. demo --json
    rc = cli_main(["demo", "--json"])
    assert rc == 0
    demo_out = json.loads(capsys.readouterr().out)
    assert demo_out["passed"] is False
    assert "TG003" in demo_out["violation_counts"]
    assert "TG004" in demo_out["violation_counts"]
    assert "TG008" in demo_out["violation_counts"]

    # 2. rules --json
    rc = cli_main(["rules", "--json"])
    assert rc == 0
    rules_out = json.loads(capsys.readouterr().out)
    assert len(rules_out) == 8

    # 3. benchmark --json
    rc = cli_main(["benchmark", "--json"])
    assert rc == 0
    bench_out = json.loads(capsys.readouterr().out)
    assert bench_out["total_scenarios"] == 40

    # 4. verify with sources file
    sources_file = tmp_path / "sources.json"
    sources_file.write_text(
        json.dumps([s.to_dict() for s in _build_benchmark_sources()]),
        encoding="utf-8",
    )
    rc = cli_main(
        [
            "verify",
            "--response",
            "TLS 1.0 is not permitted in production environments [sec_policy].",
            "--sources-json",
            str(sources_file),
            "--json",
        ]
    )
    assert rc == 0
    verify_out = json.loads(capsys.readouterr().out)
    assert verify_out["passed"] is True

    # 5. ground command
    _populate_benchmark_workspace(tmp_path)
    rc = cli_main(
        [
            "ground",
            "--response",
            "See `src/auth/jwt.py#L1-L10`.",
            "--workspace",
            str(tmp_path),
            "--json",
        ]
    )
    assert rc == 0
    ground_out = json.loads(capsys.readouterr().out)
    assert ground_out["passed"] is True


def test_mcp_server_jsonrpc_protocol(tmp_path: Path) -> None:
    _populate_benchmark_workspace(tmp_path)
    server = TruthGateMCPServer()

    # 1. initialize
    init_resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init_resp is not None
    assert init_resp["result"]["serverInfo"]["name"] == "truthgate-mcp"

    # 2. notifications/initialized returns None
    notif_resp = server.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert notif_resp is None

    # 3. ping
    ping_resp = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert ping_resp is not None
    assert ping_resp["result"] == {}

    # 4. tools/list
    list_resp = server.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    assert list_resp is not None
    tool_names = [t["name"] for t in list_resp["result"]["tools"]]
    assert tool_names == [
        "truthgate_verify_response",
        "truthgate_ground_workspace_refs",
        "truthgate_list_rules",
        "truthgate_run_benchmark",
    ]

    # 5. tools/call -> truthgate_verify_response
    verify_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "truthgate_verify_response",
                "arguments": {
                    "response_text": "TLS 1.0 is permitted in production environments [sec_policy].",
                    "sources": [s.to_dict() for s in _build_benchmark_sources()],
                },
            },
        }
    )
    assert verify_resp is not None
    payload = json.loads(verify_resp["result"]["content"][0]["text"])
    assert payload["passed"] is False
    assert "TG003" in payload["violation_counts"]

    # 6. tools/call -> truthgate_ground_workspace_refs
    ground_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "truthgate_ground_workspace_refs",
                "arguments": {
                    "response_text": "In `src/auth/jwt.py`, call `verify_token_v2()`.",
                    "workspace_root": str(tmp_path),
                },
            },
        }
    )
    assert ground_resp is not None
    ground_payload = json.loads(ground_resp["result"]["content"][0]["text"])
    assert ground_payload["passed"] is False
    assert ground_payload["verdicts"][0]["violation_code"] == "TG008"

    # 7. tools/call -> truthgate_list_rules
    rules_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "truthgate_list_rules", "arguments": {}},
        }
    )
    assert rules_resp is not None
    rules_payload = json.loads(rules_resp["result"]["content"][0]["text"])
    assert rules_payload["total_rules"] == 8

    # 8. tools/call -> truthgate_run_benchmark
    bench_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "truthgate_run_benchmark", "arguments": {}},
        }
    )
    assert bench_resp is not None
    bench_payload = json.loads(bench_resp["result"]["content"][0]["text"])
    assert bench_payload["total_scenarios"] == 40

    # 9. unknown method error
    err_resp = server.handle_request({"jsonrpc": "2.0", "id": 8, "method": "unknown/method"})
    assert err_resp is not None
    assert err_resp["error"]["code"] == -32601
