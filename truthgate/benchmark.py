"""Reproducible 40-scenario benchmark suite for TruthGate (10 grounded + 30 adversarial across TG001-TG008)."""

from __future__ import annotations

import statistics
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from truthgate.corrector import TruthGateVerifier
from truthgate.models import SourceDocument


@dataclass(slots=True)
class BenchmarkScenario:
    """Single benchmark evaluation scenario."""

    scenario_id: str
    category: str
    expected_passed: bool
    expected_rule: str
    response_text: str
    use_workspace: bool = False
    description: str = ""


def _build_benchmark_sources() -> list[SourceDocument]:
    return [
        SourceDocument(
            source_id="sec_policy",
            title="Production TLS & Cryptography Policy",
            content=(
                "TLS 1.0 is not permitted in production environments. "
                "All external endpoints must enforce TLS 1.3 with 256-bit AES-GCM ciphers. "
                "Strict mTLS client certificate validation is disabled by default on internal staging healthchecks. "
                "Database backups are encrypted at rest using KMS envelope keys."
            ),
        ),
        SourceDocument(
            source_id="sla_metrics",
            title="Q3 Edge Gateway Telemetry Report",
            content=(
                "The P95 gateway routing latency is 42.8 ms across East Africa edge nodes. "
                "Monthly cluster availability reached 99.95% uptime with 8 active worker processes per node. "
                "Token bucket rate limiting caps burst traffic at 1,200 req/s per tenant."
            ),
        ),
        SourceDocument(
            source_id="fin_ledger",
            title="Kampala Mobile Money Settlement Specification",
            content=(
                "Daily instant settlement volume is capped at UGX 1,250,000 for Tier-1 merchant accounts. "
                "Cross-border corridor reserve fee is $1,250.00 per liquidity batch. "
                "Audit ledger entries are immutable once committed to the hash chain. "
                "Settlement webhook dispatch is asynchronous with 3 automatic retries."
            ),
        ),
    ]


def _populate_benchmark_workspace(root: Path) -> None:
    """Create deterministic workspace files for AST and line-range grounding benchmarks."""
    auth_dir = root / "src" / "auth"
    ledger_dir = root / "src" / "ledger"
    gateway_dir = root / "src" / "gateway"
    auth_dir.mkdir(parents=True, exist_ok=True)
    ledger_dir.mkdir(parents=True, exist_ok=True)
    gateway_dir.mkdir(parents=True, exist_ok=True)

    jwt_lines = [
        '"""JWT token verification and payload extraction module."""',
        "",
        "import hashlib",
        "import hmac",
        "",
        "TOKEN_TTL_SECONDS = 3600",
        "",
        "",
        "class TokenValidator:",
        '    """Validates HMAC-SHA256 bearer tokens."""',
        "",
        "    def __init__(self, secret_key: str) -> None:",
        "        self.secret_key = secret_key",
        "",
        "    def verify_token(self, token: str) -> bool:",
        "        return bool(token and self.secret_key)",
        "",
        "    def revoke_session(self, session_id: str) -> bool:",
        "        return bool(session_id)",
        "",
        "",
        "def decode_payload(raw_token: str) -> dict[str, str]:",
        '    """Decode a raw JWT string into claims."""',
        '    return {"sub": raw_token}',
    ]
    (auth_dir / "jwt.py").write_text("\n".join(jwt_lines) + "\n", encoding="utf-8")

    settlement_lines = [
        '"""Immutable settlement ledger processor."""',
        "",
        "MAX_BATCH_SIZE = 500",
        "",
        "",
        "class SettlementEngine:",
        "    def commit_batch(self, batch_id: str) -> str:",
        "        return batch_id",
        "",
        "",
        "def calculate_fee(amount_ugx: int) -> int:",
        "    return int(amount_ugx * 0.01)",
    ]
    (ledger_dir / "settlement.py").write_text("\n".join(settlement_lines) + "\n", encoding="utf-8")

    router_ts_lines = [
        "// Edge gateway request router",
        "export interface RouteConfig {",
        "  path: string;",
        "  timeoutMs: number;",
        "}",
        "",
        "export class EdgeRouter {",
        "  public dispatchRequest(route: RouteConfig): boolean {",
        "    return route.timeoutMs > 0;",
        "  }",
        "}",
        "",
        "export function normalizeHeaders(raw: Record<string, string>): Record<string, string> {",
        "  return raw;",
        "}",
    ]
    (gateway_dir / "router.ts").write_text("\n".join(router_ts_lines) + "\n", encoding="utf-8")


def get_benchmark_scenarios() -> list[BenchmarkScenario]:
    """Return all 40 benchmark scenarios (10 grounded truthful + 30 adversarial hallucinations)."""
    return [
        # --- 10 Grounded Truthful Scenarios (0% False Positive Target) ---
        BenchmarkScenario(
            scenario_id="G01",
            category="grounded_rag",
            expected_passed=True,
            expected_rule="",
            response_text="TLS 1.0 is not permitted in production environments [sec_policy].",
            description="Truthful negative security policy claim.",
        ),
        BenchmarkScenario(
            scenario_id="G02",
            category="grounded_rag",
            expected_passed=True,
            expected_rule="",
            response_text="External endpoints enforce TLS 1.3 with 256-bit AES-GCM ciphers [sec_policy].",
            description="Truthful protocol version and bit-length claim.",
        ),
        BenchmarkScenario(
            scenario_id="G03",
            category="grounded_rag",
            expected_passed=True,
            expected_rule="",
            response_text="Strict mTLS client certificate validation is disabled by default on internal staging healthchecks [sec_policy].",
            description="Truthful negative-pole antonym claim.",
        ),
        BenchmarkScenario(
            scenario_id="G04",
            category="grounded_rag",
            expected_passed=True,
            expected_rule="",
            response_text="The P95 gateway routing latency is 42.8 ms across East Africa edge nodes [sla_metrics].",
            description="Truthful decimal latency metric with unit.",
        ),
        BenchmarkScenario(
            scenario_id="G05",
            category="grounded_rag",
            expected_passed=True,
            expected_rule="",
            response_text="Monthly cluster availability reached 99.95% uptime with 8 active worker processes per node [sla_metrics].",
            description="Truthful percentage and count claim.",
        ),
        BenchmarkScenario(
            scenario_id="G06",
            category="grounded_rag",
            expected_passed=True,
            expected_rule="",
            response_text="Daily instant settlement volume is capped at UGX 1,250,000 for Tier-1 merchant accounts [fin_ledger].",
            description="Truthful UGX currency limit claim.",
        ),
        BenchmarkScenario(
            scenario_id="G07",
            category="grounded_rag",
            expected_passed=True,
            expected_rule="",
            response_text="Cross-border corridor reserve fee is $1,250.00 per liquidity batch and audit ledger entries are immutable once committed [fin_ledger].",
            description="Truthful USD currency and immutability claim.",
        ),
        BenchmarkScenario(
            scenario_id="G08",
            category="grounded_code",
            expected_passed=True,
            expected_rule="",
            response_text="Token verification is implemented in [jwt.py](src/auth/jwt.py#L8-L19) via `TokenValidator.verify_token`.",
            use_workspace=True,
            description="Truthful Python file, valid line range, and existing AST method.",
        ),
        BenchmarkScenario(
            scenario_id="G09",
            category="grounded_code",
            expected_passed=True,
            expected_rule="",
            response_text="Fee calculation is defined in `src/ledger/settlement.py#L11-L12` using `calculate_fee()`.",
            use_workspace=True,
            description="Truthful workspace-only file, line range, and function reference.",
        ),
        BenchmarkScenario(
            scenario_id="G10",
            category="grounded_code",
            expected_passed=True,
            expected_rule="",
            response_text="Header normalization in `src/gateway/router.ts#L13-L15` calls `normalizeHeaders()`.",
            use_workspace=True,
            description="Truthful TypeScript file, line range, and exported function.",
        ),
        # --- TG001: Phantom Source Citations (4 scenarios) ---
        BenchmarkScenario(
            scenario_id="H01",
            category="TG001_phantom_citation",
            expected_passed=False,
            expected_rule="TG001",
            response_text="The P95 gateway routing latency is 42.8 ms across East Africa edge nodes [doc_99].",
            description="Cites non-existent [doc_99].",
        ),
        BenchmarkScenario(
            scenario_id="H02",
            category="TG001_phantom_citation",
            expected_passed=False,
            expected_rule="TG001",
            response_text="Database backups are encrypted at rest using KMS envelope keys [source:aws_kms_guide].",
            description="Cites non-existent [source:aws_kms_guide].",
        ),
        BenchmarkScenario(
            scenario_id="H03",
            category="TG001_phantom_citation",
            expected_passed=False,
            expected_rule="TG001",
            response_text="Daily instant settlement volume is capped at UGX 1,250,000 for Tier-1 merchant accounts (ref: bou_circular_2026).",
            description="Cites non-existent parenthetical reference.",
        ),
        BenchmarkScenario(
            scenario_id="H04",
            category="TG001_phantom_citation",
            expected_passed=False,
            expected_rule="TG001",
            response_text="Monthly cluster availability reached 99.95% uptime [sla_metrics, phantom_doc_2].",
            description="Multi-citation containing one valid and one phantom citation.",
        ),
        # --- TG002: Low Lexical/Entity Support (4 scenarios) ---
        BenchmarkScenario(
            scenario_id="H05",
            category="TG002_unsupported_claim",
            expected_passed=False,
            expected_rule="TG002",
            response_text="Kubernetes pod horizontal autoscaling triggers when GPU tensor utilization exceeds threshold [sec_policy].",
            description="Completely fabricated Kubernetes GPU claim attributed to sec_policy.",
        ),
        BenchmarkScenario(
            scenario_id="H06",
            category="TG002_unsupported_claim",
            expected_passed=False,
            expected_rule="TG002",
            response_text="GraphQL schema federation caches Apollo resolvers inside Redis cluster memory [sla_metrics].",
            description="Fabricated GraphQL Redis claim attributed to sla_metrics.",
        ),
        BenchmarkScenario(
            scenario_id="H07",
            category="TG002_unsupported_claim",
            expected_passed=False,
            expected_rule="TG002",
            response_text="Biometric facial liveness verification uses WebAuthn passkeys on iOS devices [fin_ledger].",
            description="Fabricated biometric passkey claim attributed to fin_ledger.",
        ),
        BenchmarkScenario(
            scenario_id="H08",
            category="TG002_unsupported_claim",
            expected_passed=False,
            expected_rule="TG002",
            response_text="BGP anycast prefix announcements fail over across submarine fiber cables automatically [sec_policy].",
            description="Fabricated BGP submarine cable claim attributed to sec_policy.",
        ),
        # --- TG003: Negation & Antonym Polarity Inversion (5 scenarios) ---
        BenchmarkScenario(
            scenario_id="H09",
            category="TG003_polarity_inversion",
            expected_passed=False,
            expected_rule="TG003",
            response_text="TLS 1.0 is permitted in production environments [sec_policy].",
            description="Drops 'not' from 'TLS 1.0 is not permitted in production environments'.",
        ),
        BenchmarkScenario(
            scenario_id="H10",
            category="TG003_polarity_inversion",
            expected_passed=False,
            expected_rule="TG003",
            response_text="Strict mTLS client certificate validation is enabled by default on internal staging healthchecks [sec_policy].",
            description="Inverts antonym 'disabled' -> 'enabled'.",
        ),
        BenchmarkScenario(
            scenario_id="H11",
            category="TG003_polarity_inversion",
            expected_passed=False,
            expected_rule="TG003",
            response_text="Database backups are unencrypted at rest using KMS envelope keys [sec_policy].",
            description="Inverts antonym 'encrypted' -> 'unencrypted'.",
        ),
        BenchmarkScenario(
            scenario_id="H12",
            category="TG003_polarity_inversion",
            expected_passed=False,
            expected_rule="TG003",
            response_text="Audit ledger entries are mutable once committed to the hash chain [fin_ledger].",
            description="Inverts antonym 'immutable' -> 'mutable'.",
        ),
        BenchmarkScenario(
            scenario_id="H13",
            category="TG003_polarity_inversion",
            expected_passed=False,
            expected_rule="TG003",
            response_text="Settlement webhook dispatch is synchronous with 3 automatic retries [fin_ledger].",
            description="Inverts antonym 'asynchronous' -> 'synchronous'.",
        ),
        # --- TG004: Numeric & Magnitude Drift (5 scenarios) ---
        BenchmarkScenario(
            scenario_id="H14",
            category="TG004_numeric_drift",
            expected_passed=False,
            expected_rule="TG004",
            response_text="The P95 gateway routing latency is 4.28 ms across East Africa edge nodes [sla_metrics].",
            description="10x decimal shift on latency: 42.8 ms -> 4.28 ms.",
        ),
        BenchmarkScenario(
            scenario_id="H15",
            category="TG004_numeric_drift",
            expected_passed=False,
            expected_rule="TG004",
            response_text="Daily instant settlement volume is capped at UGX 125,000 for Tier-1 merchant accounts [fin_ledger].",
            description="10x magnitude drop on currency: UGX 1,250,000 -> UGX 125,000.",
        ),
        BenchmarkScenario(
            scenario_id="H16",
            category="TG004_numeric_drift",
            expected_passed=False,
            expected_rule="TG004",
            response_text="Monthly cluster availability reached 99.99% uptime with 8 active worker processes per node [sla_metrics].",
            description="SLA percentage inflation: 99.95% -> 99.99%.",
        ),
        BenchmarkScenario(
            scenario_id="H17",
            category="TG004_numeric_drift",
            expected_passed=False,
            expected_rule="TG004",
            response_text="Cross-border corridor reserve fee is $12,500.00 per liquidity batch [fin_ledger].",
            description="10x USD currency inflation: $1,250.00 -> $12,500.00.",
        ),
        BenchmarkScenario(
            scenario_id="H18",
            category="TG004_numeric_drift",
            expected_passed=False,
            expected_rule="TG004",
            response_text="Token bucket rate limiting caps burst traffic at 12,000 req/s per tenant [sla_metrics].",
            description="10x throughput drift: 1,200 req/s -> 12,000 req/s.",
        ),
        # --- TG005: Uncited Factual Assertions (3 scenarios) ---
        BenchmarkScenario(
            scenario_id="H19",
            category="TG005_uncited_claim",
            expected_passed=False,
            expected_rule="TG005",
            response_text="The P95 gateway routing latency is 42.8 ms across East Africa edge nodes.",
            description="Factual numeric claim missing any [source_id] citation.",
        ),
        BenchmarkScenario(
            scenario_id="H20",
            category="TG005_uncited_claim",
            expected_passed=False,
            expected_rule="TG005",
            response_text="Daily instant settlement volume is capped at UGX 1,250,000 for Tier-1 merchant accounts.",
            description="Factual currency limit missing any citation.",
        ),
        BenchmarkScenario(
            scenario_id="H21",
            category="TG005_uncited_claim",
            expected_passed=False,
            expected_rule="TG005",
            response_text="All external endpoints must enforce TLS 1.3 with 256-bit AES-GCM ciphers.",
            description="Security policy assertion missing any citation.",
        ),
        # --- TG006: Non-Existent File Path (3 scenarios) ---
        BenchmarkScenario(
            scenario_id="H22",
            category="TG006_missing_file",
            expected_passed=False,
            expected_rule="TG006",
            response_text="OAuth2 token introspection is implemented in `src/auth/oauth2_provider.py`.",
            use_workspace=True,
            description="References non-existent Python file src/auth/oauth2_provider.py.",
        ),
        BenchmarkScenario(
            scenario_id="H23",
            category="TG006_missing_file",
            expected_passed=False,
            expected_rule="TG006",
            response_text="Ledger reconciliation runs inside [reconcile.py](src/ledger/reconcile.py#L10-L25).",
            use_workspace=True,
            description="Markdown link to non-existent file src/ledger/reconcile.py.",
        ),
        BenchmarkScenario(
            scenario_id="H24",
            category="TG006_missing_file",
            expected_passed=False,
            expected_rule="TG006",
            response_text="Rate limiter middleware is configured in [src/gateway/rate_limit.ts#L1-L20].",
            use_workspace=True,
            description="Bracketed reference to non-existent TypeScript file.",
        ),
        # --- TG007: Out-of-Bounds Line Range (3 scenarios) ---
        BenchmarkScenario(
            scenario_id="H25",
            category="TG007_line_range",
            expected_passed=False,
            expected_rule="TG007",
            response_text="Token verification logic is located in [src/auth/jwt.py#L120-L150].",
            use_workspace=True,
            description="References lines 120-150 in a 24-line file.",
        ),
        BenchmarkScenario(
            scenario_id="H26",
            category="TG007_line_range",
            expected_passed=False,
            expected_rule="TG007",
            response_text="Settlement batch commitment is defined in [settlement.py](src/ledger/settlement.py#L40-L60).",
            use_workspace=True,
            description="References lines 40-60 in a 12-line file.",
        ),
        BenchmarkScenario(
            scenario_id="H27",
            category="TG007_line_range",
            expected_passed=False,
            expected_rule="TG007",
            response_text="Edge routing dispatch is implemented at `src/gateway/router.ts#L80-L110`.",
            use_workspace=True,
            description="References lines 80-110 in a 15-line TypeScript file.",
        ),
        # --- TG008: Phantom Code Symbol (3 scenarios) ---
        BenchmarkScenario(
            scenario_id="H28",
            category="TG008_phantom_symbol",
            expected_passed=False,
            expected_rule="TG008",
            response_text="In `src/auth/jwt.py`, call `verify_token_v2()` to validate bearer tokens.",
            use_workspace=True,
            description="References hallucinated function verify_token_v2() in src/auth/jwt.py.",
        ),
        BenchmarkScenario(
            scenario_id="H29",
            category="TG008_phantom_symbol",
            expected_passed=False,
            expected_rule="TG008",
            response_text="In `src/ledger/settlement.py`, invoke `SettlementEngine.rollback_transaction()` on failure.",
            use_workspace=True,
            description="References hallucinated method SettlementEngine.rollback_transaction.",
        ),
        BenchmarkScenario(
            scenario_id="H30",
            category="TG008_phantom_symbol",
            expected_passed=False,
            expected_rule="TG008",
            response_text="In `src/gateway/router.ts`, use `verifySignatureHmac()` before routing.",
            use_workspace=True,
            description="References hallucinated function verifySignatureHmac in src/gateway/router.ts.",
        ),
    ]


def run_benchmark_suite() -> dict[str, Any]:
    """Execute the 40-scenario TruthGate benchmark suite and return deterministic metrics."""
    sources = _build_benchmark_sources()
    scenarios = get_benchmark_scenarios()
    verifier = TruthGateVerifier()

    tp = 0  # Hallucination correctly caught (expected_passed=False, actual_passed=False)
    fp = 0  # Grounded response falsely flagged (expected_passed=True, actual_passed=False)
    tn = 0  # Grounded response correctly passed (expected_passed=True, actual_passed=True)
    fn = 0  # Hallucination missed (expected_passed=False, actual_passed=True)
    exact_rule_matches = 0

    latencies_ms: list[float] = []
    by_rule: dict[str, dict[str, int]] = {
        f"TG00{i}": {"total": 0, "caught": 0, "exact_code_match": 0} for i in range(1, 9)
    }
    scenario_results: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="truthgate_bench_") as tmp_dir:
        ws_root = Path(tmp_dir)
        _populate_benchmark_workspace(ws_root)
        # Warm up workspace index once before timing per-response verification
        resolved_ws = verifier.grounder._resolve_root(ws_root)
        verifier.grounder._get_workspace_index(resolved_ws)

        for sc in scenarios:
            t0 = time.perf_counter()
            res = verifier.verify(
                response_text=sc.response_text,
                sources=sources,
                workspace_root=ws_root if sc.use_workspace else None,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)

            detected_rules = sorted(res.violation_counts.keys())
            if sc.expected_passed:
                if res.passed:
                    tn += 1
                    exact_rule_matches += 1
                else:
                    fp += 1
            else:
                by_rule[sc.expected_rule]["total"] += 1
                if not res.passed:
                    tp += 1
                    by_rule[sc.expected_rule]["caught"] += 1
                    if sc.expected_rule in detected_rules:
                        exact_rule_matches += 1
                        by_rule[sc.expected_rule]["exact_code_match"] += 1
                else:
                    fn += 1

            scenario_results.append(
                {
                    "scenario_id": sc.scenario_id,
                    "category": sc.category,
                    "expected_passed": sc.expected_passed,
                    "actual_passed": res.passed,
                    "expected_rule": sc.expected_rule,
                    "detected_rules": detected_rules,
                    "elapsed_ms": round(elapsed_ms, 4),
                }
            )

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
    sorted_lat = sorted(latencies_ms)
    p50 = statistics.median(sorted_lat)
    p95_idx = min(len(sorted_lat) - 1, int(len(sorted_lat) * 0.95))
    p95 = sorted_lat[p95_idx]

    return {
        "benchmark": "TruthGate 40-Scenario Hallucination & Grounding Suite v1.0",
        "total_scenarios": len(scenarios),
        "grounded_scenarios": tn + fp,
        "adversarial_scenarios": tp + fn,
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1_score": round(f1, 4),
        "false_positive_rate": round(fpr, 4),
        "exact_rule_accuracy": round(exact_rule_matches / len(scenarios), 4),
        "p50_latency_ms": round(p50, 4),
        "p95_latency_ms": round(p95, 4),
        "mean_latency_ms": round(statistics.mean(sorted_lat), 4),
        "by_rule": by_rule,
        "scenarios": scenario_results,
    }
