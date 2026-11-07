"""Command-Line Interface for TruthGate (demo, verify, ground, benchmark, rules, mcp)."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from truthgate.benchmark import _build_benchmark_sources, _populate_benchmark_workspace, run_benchmark_suite
from truthgate.corrector import TruthGateVerifier
from truthgate.grounding import WorkspaceSymbolGrounder
from truthgate.mcp_server import TruthGateMCPServer
from truthgate.models import RULES, SourceDocument


def _cmd_demo(args: argparse.Namespace) -> int:
    sources = _build_benchmark_sources()
    flawed_response = (
        "TLS 1.0 is permitted in production environments [sec_policy]. "
        "The P95 gateway routing latency is 4.28 ms across East Africa edge nodes [sla_metrics]. "
        "Daily instant settlement volume is capped at UGX 1,250,000 for Tier-1 merchant accounts [fin_ledger]. "
        "In `src/auth/jwt.py`, call `verify_token_v2()` to validate bearer tokens."
    )

    with tempfile.TemporaryDirectory(prefix="truthgate_demo_") as tmp_dir:
        ws_root = Path(tmp_dir)
        _populate_benchmark_workspace(ws_root)
        verifier = TruthGateVerifier()
        result = verifier.verify(
            response_text=flawed_response,
            sources=sources,
            workspace_root=ws_root,
        )

    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), indent=2))
        return 0

    print("=" * 84)
    print("TRUTHGATE v1.0.0 — DETERMINISTIC AGENT HALLUCINATION & GROUNDING AUDIT DEMO")
    print("=" * 84)
    print("\n[Agent Response Under Audit]")
    print(f"  {flawed_response}\n")
    print(
        f"[Audit Summary] Passed: {result.passed} | Score: {result.overall_score:.2f} | "
        f"Claims: {result.supported_claims}/{result.total_claims} supported | "
        f"Latency: {result.elapsed_ms:.3f} ms"
    )
    print("\n[Claim-by-Claim Verdicts]")
    for v in result.claim_verdicts:
        badge = "PASS" if v.passed else f"FAIL:{v.violation_code}"
        print(f"  - [{badge}] ({v.status}) {v.claim_text}")
        print(f"      Reason: {v.explanation}")
    print("\n[Compiled Surgical Self-Correction Prompt]")
    print("-" * 84)
    print(result.repair_prompt)
    print("-" * 84)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    sources: list[SourceDocument] = []
    if args.sources_json:
        raw_data = json.loads(Path(args.sources_json).read_text(encoding="utf-8"))
        for item in raw_data:
            sources.append(
                SourceDocument(
                    source_id=str(item["source_id"]),
                    title=str(item.get("title", item["source_id"])),
                    content=str(item["content"]),
                    metadata=dict(item.get("metadata") or {}),
                )
            )

    verifier = TruthGateVerifier(strict_citations=not args.no_strict_citations)
    result = verifier.verify(
        response_text=args.response,
        sources=sources,
        workspace_root=args.workspace,
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(
            f"TruthGate Verification: passed={result.passed} "
            f"({result.supported_claims}/{result.total_claims} claims supported, {result.elapsed_ms:.3f} ms)"
        )
        for v in result.claim_verdicts:
            print(f"  [{v.status}] {v.claim_id}: {v.explanation}")
        if result.repair_prompt:
            print("\n" + result.repair_prompt)

    return 0 if (result.passed or not args.fail_on_violation) else 1


def _cmd_ground(args: argparse.Namespace) -> int:
    grounder = WorkspaceSymbolGrounder()
    report = grounder.ground_text(args.response, args.workspace)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(
            f"Workspace Grounding: passed={report.passed} | "
            f"valid={report.valid_references}/{report.total_references}"
        )
        for v in report.verdicts:
            print(f"  [{v.status}] {v.claim_id}: {v.explanation}")
    return 0 if (report.passed or not args.fail_on_violation) else 1


def _cmd_benchmark(args: argparse.Namespace) -> int:
    summary = run_benchmark_suite()
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print("=" * 78)
    print(f"{summary['benchmark']}")
    print("=" * 78)
    print(f"Total Scenarios       : {summary['total_scenarios']} (10 Grounded, 30 Adversarial)")
    print(f"True Positives (TP)   : {summary['true_positives']}/30")
    print(f"True Negatives (TN)   : {summary['true_negatives']}/10")
    print(f"False Positives (FP)  : {summary['false_positives']}/10")
    print(f"False Negatives (FN)  : {summary['false_negatives']}/30")
    print(f"Precision             : {summary['precision'] * 100:.1f}%")
    print(f"Recall                : {summary['recall'] * 100:.1f}%")
    print(f"F1 Score              : {summary['f1_score'] * 100:.1f}%")
    print(f"False Positive Rate   : {summary['false_positive_rate'] * 100:.1f}%")
    print(f"Exact Rule Accuracy   : {summary['exact_rule_accuracy'] * 100:.1f}%")
    print(
        f"Verification Latency  : P50 = {summary['p50_latency_ms']:.3f} ms | "
        f"P95 = {summary['p95_latency_ms']:.3f} ms"
    )
    print("\nPer-Rule Detection Breakdown:")
    for code, stats in summary["by_rule"].items():
        print(
            f"  {code}: {stats['caught']}/{stats['total']} caught "
            f"({stats['exact_code_match']}/{stats['total']} exact rule code)"
        )
    return 0


def _cmd_rules(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps([r.to_dict() for r in RULES], indent=2))
        return 0

    print("TruthGate Deterministic Verification Rules (TG001-TG008):")
    for r in RULES:
        print(f"  {r.code} [{r.severity}] {r.name} ({r.default_status})")
        print(f"        {r.description}")
    return 0


def _cmd_mcp(_args: argparse.Namespace) -> int:
    server = TruthGateMCPServer()
    server.serve_stdio()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="truthgate",
        description="Deterministic Agent Hallucination Auditor, Citation-Span Entailment Verifier & Workspace Symbol Grounding Engine.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_demo = sub.add_parser("demo", help="Run an end-to-end audit and self-correction demo.")
    p_demo.add_argument("--json", action="store_true", help="Emit JSON output.")
    p_demo.set_defaults(func=_cmd_demo)

    p_verify = sub.add_parser("verify", help="Verify an agent response against sources and/or a workspace.")
    p_verify.add_argument("--response", required=True, help="Agent response markdown string.")
    p_verify.add_argument("--sources-json", help="Path to JSON file containing source documents.")
    p_verify.add_argument("--workspace", help="Optional workspace root directory path.")
    p_verify.add_argument("--no-strict-citations", action="store_true", help="Disable TG005 uncited claim check.")
    p_verify.add_argument("--fail-on-violation", action="store_true", help="Exit with code 1 if verification fails.")
    p_verify.add_argument("--json", action="store_true", help="Emit JSON output.")
    p_verify.set_defaults(func=_cmd_verify)

    p_ground = sub.add_parser("ground", help="Verify workspace file paths, line ranges, and AST symbols.")
    p_ground.add_argument("--response", required=True, help="Markdown text containing code references.")
    p_ground.add_argument("--workspace", required=True, help="Workspace directory path.")
    p_ground.add_argument("--fail-on-violation", action="store_true", help="Exit with code 1 if grounding fails.")
    p_ground.add_argument("--json", action="store_true", help="Emit JSON output.")
    p_ground.set_defaults(func=_cmd_ground)

    p_bench = sub.add_parser("benchmark", help="Run the reproducible 40-scenario benchmark suite.")
    p_bench.add_argument("--json", action="store_true", help="Emit JSON benchmark report.")
    p_bench.set_defaults(func=_cmd_benchmark)

    p_rules = sub.add_parser("rules", help="List all 8 deterministic verification rules (TG001-TG008).")
    p_rules.add_argument("--json", action="store_true", help="Emit JSON rule definitions.")
    p_rules.set_defaults(func=_cmd_rules)

    p_mcp = sub.add_parser("mcp", help="Start the JSON-RPC 2.0 Stdio MCP server.")
    p_mcp.set_defaults(func=_cmd_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
