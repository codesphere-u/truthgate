"""Zero-dependency HTTP, Streamable MCP, REST API, and Developer Workbench Server for TruthGate."""

from __future__ import annotations

import json
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from truthgate.benchmark import (
    _build_benchmark_sources,
    _populate_benchmark_workspace,
    run_benchmark_suite,
)
from truthgate.corrector import TruthGateVerifier
from truthgate.decomposer import ClaimDecomposer
from truthgate.grounding import WorkspaceSymbolGrounder
from truthgate.mcp_server import (
    MCP_PROMPTS,
    MCP_PROTOCOL_VERSION,
    MCP_RESOURCES,
    MCP_TOOLS,
    TruthGateMCPServer,
)
from truthgate.models import RULES, SourceDocument

DEFAULT_MAX_BODY_BYTES = 1_048_576
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_VERSION = "1.0.0"


class PayloadTooLargeError(ValueError):
    """Raised when an incoming HTTP request exceeds the configured max body size."""


class BadRequestError(ValueError):
    """Raised when an incoming HTTP request has malformed headers or JSON payload."""


@dataclass(frozen=True)
class HTTPResponse:
    """Framework-agnostic HTTP response envelope."""

    status: int
    headers: dict[str, str]
    body: bytes


class MetricsCollector:
    """Thread-safe HTTP, MCP, and verification telemetry collector."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._requests_total = 0
        self._errors_total = 0
        self._total_latency_ms = 0.0
        self._status_counts: dict[int, int] = {}
        self._method_counts: dict[str, int] = {}
        self._mcp_method_counts: dict[str, int] = {}
        self._audits_total = 0
        self._audits_passed = 0
        self._audits_failed = 0
        self._groundings_total = 0
        self._benchmarks_total = 0

    def record_http(self, method: str, status: int, elapsed_ms: float) -> None:
        with self._lock:
            self._requests_total += 1
            if status >= 500:
                self._errors_total += 1
            self._total_latency_ms += elapsed_ms
            self._status_counts[status] = self._status_counts.get(status, 0) + 1
            self._method_counts[method] = self._method_counts.get(method, 0) + 1

    def record_mcp(self, method: str) -> None:
        if not method:
            return
        with self._lock:
            self._mcp_method_counts[method] = self._mcp_method_counts.get(method, 0) + 1

    def record_audit(self, passed: bool) -> None:
        with self._lock:
            self._audits_total += 1
            if passed:
                self._audits_passed += 1
            else:
                self._audits_failed += 1

    def record_grounding(self) -> None:
        with self._lock:
            self._groundings_total += 1

    def record_benchmark(self) -> None:
        with self._lock:
            self._benchmarks_total += 1

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            uptime = max(0.0, time.monotonic() - self._started_at)
            avg_ms = self._total_latency_ms / self._requests_total if self._requests_total else 0.0
            return {
                "service": "truthgate",
                "version": _VERSION,
                "uptime_seconds": round(uptime, 3),
                "requests_total": self._requests_total,
                "errors_total": self._errors_total,
                "avg_latency_ms": round(avg_ms, 4),
                "status_counts": {str(k): v for k, v in sorted(self._status_counts.items())},
                "method_counts": dict(sorted(self._method_counts.items())),
                "mcp_method_counts": dict(sorted(self._mcp_method_counts.items())),
                "audits": {
                    "total": self._audits_total,
                    "passed": self._audits_passed,
                    "failed": self._audits_failed,
                },
                "groundings_total": self._groundings_total,
                "benchmarks_total": self._benchmarks_total,
            }

    def to_prometheus(self) -> str:
        snap = self.to_dict()
        lines = [
            "# HELP truthgate_uptime_seconds Seconds since TruthGate HTTP server started.",
            "# TYPE truthgate_uptime_seconds gauge",
            f"truthgate_uptime_seconds {snap['uptime_seconds']}",
            "# HELP truthgate_http_requests_total Total HTTP requests processed.",
            "# TYPE truthgate_http_requests_total counter",
            f"truthgate_http_requests_total {snap['requests_total']}",
            "# HELP truthgate_http_errors_total Total HTTP 5xx errors.",
            "# TYPE truthgate_http_errors_total counter",
            f"truthgate_http_errors_total {snap['errors_total']}",
            "# HELP truthgate_audits_total Total response audits executed via REST.",
            "# TYPE truthgate_audits_total counter",
            f"truthgate_audits_total {snap['audits']['total']}",
            "# HELP truthgate_audits_passed_total Total passed response audits.",
            "# TYPE truthgate_audits_passed_total counter",
            f"truthgate_audits_passed_total {snap['audits']['passed']}",
            "# HELP truthgate_audits_failed_total Total failed response audits.",
            "# TYPE truthgate_audits_failed_total counter",
            f"truthgate_audits_failed_total {snap['audits']['failed']}",
            "# HELP truthgate_groundings_total Total workspace symbol groundings executed.",
            "# TYPE truthgate_groundings_total counter",
            f"truthgate_groundings_total {snap['groundings_total']}",
            "# HELP truthgate_benchmarks_total Total benchmark suite runs.",
            "# TYPE truthgate_benchmarks_total counter",
            f"truthgate_benchmarks_total {snap['benchmarks_total']}",
        ]
        for status_code, count in snap["status_counts"].items():
            lines.append(
                f'truthgate_http_responses_by_status{{status="{status_code}"}} {count}'
            )
        for mcp_method, count in snap["mcp_method_counts"].items():
            safe_method = mcp_method.replace('"', '\\"')
            lines.append(f'truthgate_mcp_calls_total{{method="{safe_method}"}} {count}')
        return "\n".join(lines) + "\n"


def sanitize_request_id(raw_value: str | None) -> str:
    """Validate and sanitize an incoming X-Request-ID or generate a fresh hex UUID."""
    if raw_value is not None:
        stripped = raw_value.strip()
        if _REQUEST_ID_RE.fullmatch(stripped):
            return stripped
    return uuid.uuid4().hex


def _get_header(headers: Mapping[str, str], name: str, default: str = "") -> str:
    target = name.lower()
    for key, val in headers.items():
        if key.lower() == target:
            return val
    return default


def parse_content_length(headers: Mapping[str, str]) -> int | None:
    """Parse Content-Length header safely, rejecting negative or malformed values."""
    raw = _get_header(headers, "content-length", "")
    if not raw:
        return None
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise BadRequestError("Invalid Content-Length header.") from exc
    if value < 0:
        raise BadRequestError("Negative Content-Length header.")
    return value


def build_security_headers(
    path: str,
    request_id: str,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build hardened response headers including CORS, security controls, and X-Request-ID."""
    headers: dict[str, str] = {
        "X-Request-ID": request_id,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "X-Request-ID, Mcp-Session-Id",
    }
    if path.startswith("/api/") or path in ("/api", "/mcp", "/messages"):
        headers["Cache-Control"] = "no-store"
    if extra:
        headers.update(extra)
    return headers


def _render_workbench_html() -> str:
    """Return the dark-mode interactive TruthGate Developer Workbench HTML document."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>TruthGate Developer Workbench</title>
  <style>
    :root {
      --bg: #0b0f17;
      --surface: #131a26;
      --surface-2: #192231;
      --border: #273449;
      --text: #e2e8f0;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --ok: #10b981;
      --err: #f43f5e;
      --warn: #f59e0b;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: var(--sans);
      background: radial-gradient(circle at top right, #162238, var(--bg) 55%);
      color: var(--text);
      line-height: 1.5;
    }
    header {
      border-bottom: 1px solid var(--border);
      background: rgba(11, 15, 23, 0.9);
      padding: 16px 24px;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .badge {
      font-family: var(--mono);
      font-size: 12px;
      padding: 3px 9px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: var(--surface-2);
      color: var(--accent);
    }
    .badge.ok { color: var(--ok); border-color: rgba(16, 185, 129, 0.4); }
    .badge.fail { color: var(--err); border-color: rgba(244, 63, 94, 0.4); }
    main { max-width: 1240px; margin: 0 auto; padding: 24px; display: grid; gap: 20px; }
    .grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 18px; }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px;
      box-shadow: 0 8px 28px rgba(0, 0, 0, 0.25);
    }
    h1 { margin: 0; font-size: 20px; letter-spacing: -0.02em; }
    h2 { margin: 0 0 12px 0; font-size: 15px; color: #f8fafc; display: flex; justify-content: space-between; align-items: center; }
    label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 6px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
    textarea, input[type="text"] {
      width: 100%;
      background: #090d14;
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 12px;
      font-family: var(--mono);
      font-size: 12.5px;
      resize: vertical;
    }
    .row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-top: 12px; }
    button {
      background: var(--accent);
      color: #06121f;
      border: none;
      border-radius: 8px;
      padding: 9px 14px;
      font-weight: 700;
      font-size: 13px;
      cursor: pointer;
    }
    button.secondary {
      background: var(--surface-2);
      color: var(--text);
      border: 1px solid var(--border);
    }
    button:disabled { opacity: 0.55; cursor: wait; }
    .metrics-strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 14px; }
    .stat { background: var(--surface-2); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
    .stat .k { font-size: 11px; color: var(--muted); text-transform: uppercase; }
    .stat .v { font-family: var(--mono); font-size: 18px; font-weight: 700; margin-top: 2px; }
    table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
    th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
    th { color: var(--muted); font-size: 11px; text-transform: uppercase; }
    pre {
      background: #090d14;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      font-family: var(--mono);
      font-size: 12px;
      overflow-x: auto;
      white-space: pre-wrap;
      margin: 0;
    }
    .muted { color: var(--muted); font-size: 12.5px; }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <h1>TruthGate</h1>
      <span class="badge">v1.0.0</span>
      <span class="badge ok" id="health-pill">Checking /health...</span>
    </div>
    <div class="row" style="margin-top:0">
      <button class="secondary" onclick="loadAdversarialDemo()">Load Adversarial Demo</button>
      <button class="secondary" onclick="loadGroundedDemo()">Load Grounded Preset</button>
      <button onclick="runBenchmark()">Run 40-Scenario Benchmark</button>
    </div>
  </header>

  <main>
    <div class="grid-2">
      <section class="card">
        <h2><span>Agent Response Under Audit</span><span class="muted">POST /api/verify</span></h2>
        <label for="response-text">Agent Markdown Response</label>
        <textarea id="response-text" rows="5"></textarea>
        <div class="row">
          <div style="flex:1;min-width:200px">
            <label for="workspace-root">Workspace Root (Optional, TG006-TG008)</label>
            <input type="text" id="workspace-root" placeholder="Leave blank for RAG-only or enter workspace path" />
          </div>
          <label style="display:flex;align-items:center;gap:6px;margin-top:18px;text-transform:none">
            <input type="checkbox" id="strict-citations" checked /> Strict Citations (TG005)
          </label>
        </div>
        <div class="row">
          <button id="verify-btn" onclick="runVerify()">Run Deterministic Audit</button>
          <button class="secondary" onclick="runGround()">Ground Workspace Refs Only</button>
          <button class="secondary" onclick="inspectMcp()">Inspect MCP Capabilities</button>
        </div>
      </section>

      <section class="card">
        <h2><span>Source Corpus (JSON)</span><span class="muted">Rules TG001-TG005</span></h2>
        <label for="sources-json">SourceDocuments Array</label>
        <textarea id="sources-json" rows="9"></textarea>
      </section>
    </div>

    <section class="card">
      <h2><span>Audit Verdict & Surgical Self-Correction</span><span id="audit-badge" class="badge">Ready</span></h2>
      <div class="metrics-strip">
        <div class="stat"><div class="k">Passed</div><div class="v" id="st-passed">-</div></div>
        <div class="stat"><div class="k">Score</div><div class="v" id="st-score">-</div></div>
        <div class="stat"><div class="k">Claims</div><div class="v" id="st-claims">-</div></div>
        <div class="stat"><div class="k">Latency</div><div class="v" id="st-latency">-</div></div>
        <div class="stat"><div class="k">Violations</div><div class="v" id="st-violations">-</div></div>
      </div>
      <div id="verdicts-container" class="muted">Run an audit to view claim-by-claim verdicts.</div>
      <div style="margin-top:14px">
        <label>Compiled Surgical Repair Prompt</label>
        <pre id="repair-prompt">No violations detected yet.</pre>
      </div>
    </section>

    <div class="grid-2">
      <section class="card">
        <h2><span>40-Scenario Benchmark Suite</span><span class="muted">POST /api/benchmark</span></h2>
        <pre id="benchmark-output">Click "Run 40-Scenario Benchmark" to execute precision/recall & latency evaluation.</pre>
      </section>
      <section class="card">
        <h2><span>Deterministic Rule Matrix (TG001-TG008)</span><span class="muted">GET /api/rules</span></h2>
        <div id="rules-table" class="muted">Loading rules...</div>
      </section>
    </div>
  </main>

  <script>
    const ADVERSARIAL_RESPONSE =
      "TLS 1.0 is permitted in production environments [sec_policy]. " +
      "The P95 gateway routing latency is 4.28 ms across East Africa edge nodes [sla_metrics]. " +
      "Daily instant settlement volume is capped at UGX 1,250,000 for Tier-1 merchant accounts [fin_ledger].";

    const GROUNDED_RESPONSE =
      "TLS 1.0 is not permitted in production environments [sec_policy]. " +
      "The P95 gateway routing latency is 42.8 ms across East Africa edge nodes [sla_metrics]. " +
      "Daily instant settlement volume is capped at UGX 1,250,000 for Tier-1 merchant accounts [fin_ledger].";

    const DEFAULT_SOURCES = [
      {
        source_id: "sec_policy",
        title: "Production TLS & Cryptography Policy",
        content: "TLS 1.0 is not permitted in production environments. All external endpoints must enforce TLS 1.3 with 256-bit AES-GCM ciphers."
      },
      {
        source_id: "sla_metrics",
        title: "Q3 Edge Gateway Telemetry Report",
        content: "The P95 gateway routing latency is 42.8 ms across East Africa edge nodes. Monthly cluster availability reached 99.95% uptime."
      },
      {
        source_id: "fin_ledger",
        title: "Kampala Mobile Money Settlement Spec",
        content: "Daily instant settlement volume is capped at UGX 1,250,000 for Tier-1 merchant accounts."
      }
    ];

    function loadAdversarialDemo() {
      document.getElementById("response-text").value = ADVERSARIAL_RESPONSE;
      document.getElementById("sources-json").value = JSON.stringify(DEFAULT_SOURCES, null, 2);
      runVerify();
    }

    function loadGroundedDemo() {
      document.getElementById("response-text").value = GROUNDED_RESPONSE;
      document.getElementById("sources-json").value = JSON.stringify(DEFAULT_SOURCES, null, 2);
      runVerify();
    }

    async function runVerify() {
      const btn = document.getElementById("verify-btn");
      btn.disabled = true;
      try {
        const sources = JSON.parse(document.getElementById("sources-json").value || "[]");
        const ws = document.getElementById("workspace-root").value.trim() || null;
        const resp = await fetch("/api/verify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            response_text: document.getElementById("response-text").value,
            sources,
            workspace_root: ws,
            strict_citations: document.getElementById("strict-citations").checked
          })
        });
        const data = await resp.json();
        renderAuditResult(data);
      } catch (err) {
        document.getElementById("repair-prompt").textContent = "Error: " + err.message;
      } finally {
        btn.disabled = false;
      }
    }

    async function runGround() {
      const ws = document.getElementById("workspace-root").value.trim() || ".";
      const resp = await fetch("/api/ground", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          response_text: document.getElementById("response-text").value,
          workspace_root: ws
        })
      });
      const data = await resp.json();
      document.getElementById("repair-prompt").textContent = JSON.stringify(data, null, 2);
    }

    function renderAuditResult(data) {
      if (data.error) {
        document.getElementById("repair-prompt").textContent = JSON.stringify(data, null, 2);
        return;
      }
      const badge = document.getElementById("audit-badge");
      badge.textContent = data.passed ? "PASSED" : "VIOLATIONS DETECTED";
      badge.className = "badge " + (data.passed ? "ok" : "fail");
      document.getElementById("st-passed").textContent = String(data.passed);
      document.getElementById("st-score").textContent = (data.overall_score * 100).toFixed(0) + "%";
      document.getElementById("st-claims").textContent = `${data.supported_claims}/${data.total_claims}`;
      document.getElementById("st-latency").textContent = `${data.elapsed_ms.toFixed(3)} ms`;
      const vKeys = Object.keys(data.violation_counts || {});
      document.getElementById("st-violations").textContent = vKeys.length ? vKeys.join(", ") : "None";

      const rows = (data.claim_verdicts || []).map(v => {
        const cls = v.passed ? "ok" : "fail";
        const tag = v.passed ? "PASS" : (v.violation_code || v.status);
        return `<tr>
          <td><span class="badge ${cls}">${tag}</span></td>
          <td>${escapeHtml(v.claim_text)}</td>
          <td>${escapeHtml(v.explanation)}</td>
        </tr>`;
      }).join("");
      document.getElementById("verdicts-container").innerHTML =
        `<table><thead><tr><th>Verdict</th><th>Claim</th><th>Diagnostic</th></tr></thead><tbody>${rows}</tbody></table>`;
      document.getElementById("repair-prompt").textContent = data.repair_prompt || "All claims verified cleanly.";
    }

    async function runBenchmark() {
      const el = document.getElementById("benchmark-output");
      el.textContent = "Running 40-scenario benchmark...";
      const resp = await fetch("/api/benchmark", { method: "POST" });
      const data = await resp.json();
      el.textContent = JSON.stringify({
        benchmark: data.benchmark,
        total_scenarios: data.total_scenarios,
        precision: data.precision,
        recall: data.recall,
        f1_score: data.f1_score,
        false_positive_rate: data.false_positive_rate,
        exact_rule_accuracy: data.exact_rule_accuracy,
        p50_latency_ms: data.p50_latency_ms,
        p95_latency_ms: data.p95_latency_ms,
        by_rule: data.by_rule
      }, null, 2);
    }

    async function inspectMcp() {
      const [tools, resources, prompts] = await Promise.all([
        fetch("/mcp", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({jsonrpc:"2.0",id:1,method:"tools/list"}) }).then(r => r.json()),
        fetch("/mcp", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({jsonrpc:"2.0",id:2,method:"resources/list"}) }).then(r => r.json()),
        fetch("/mcp", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({jsonrpc:"2.0",id:3,method:"prompts/list"}) }).then(r => r.json())
      ]);
      document.getElementById("benchmark-output").textContent = JSON.stringify({
        tools: tools.result.tools.map(t => t.name),
        resources: resources.result.resources.map(r => r.uri),
        prompts: prompts.result.prompts.map(p => p.name)
      }, null, 2);
    }

    function escapeHtml(str) {
      return String(str).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
    }

    async function boot() {
      document.getElementById("response-text").value = ADVERSARIAL_RESPONSE;
      document.getElementById("sources-json").value = JSON.stringify(DEFAULT_SOURCES, null, 2);
      try {
        const h = await fetch("/health").then(r => r.json());
        document.getElementById("health-pill").textContent = `status: ${h.status} | uptime: ${h.uptime_seconds}s`;
      } catch {
        document.getElementById("health-pill").textContent = "offline";
      }
      const rules = await fetch("/api/rules").then(r => r.json());
      const rows = (rules.rules || []).map(r =>
        `<tr><td><code>${r.code}</code></td><td>${escapeHtml(r.name)}</td><td>${r.severity}</td><td>${escapeHtml(r.description)}</td></tr>`
      ).join("");
      document.getElementById("rules-table").innerHTML =
        `<table><thead><tr><th>Rule</th><th>Name</th><th>Severity</th><th>Check</th></tr></thead><tbody>${rows}</tbody></table>`;
    }
    boot();
  </script>
</body>
</html>
"""


WORKBENCH_HTML = _render_workbench_html()


class TruthGateHTTPApp:
    """Framework-agnostic HTTP, Streamable MCP, and REST router for TruthGate."""

    def __init__(self, *, max_body_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be > 0")
        self.max_body_bytes = max_body_bytes
        self.mcp_server = TruthGateMCPServer()
        self.decomposer = ClaimDecomposer()
        self.grounder = WorkspaceSymbolGrounder()
        self.metrics = MetricsCollector()
        self._sessions: dict[str, float] = {}
        self._route_table: dict[str, dict[str, Callable[..., HTTPResponse]]] = {
            "/": {"GET": self._handle_root},
            "/health": {"GET": self._handle_health},
            "/healthz": {"GET": self._handle_health},
            "/readyz": {"GET": self._handle_readyz},
            "/info": {"GET": self._handle_info},
            "/metrics": {"GET": self._handle_metrics},
            "/mcp": {"GET": self._handle_mcp_get, "POST": self._handle_mcp_post},
            "/messages": {"POST": self._handle_mcp_post},
            "/api/rules": {"GET": self._handle_api_rules},
            "/api/verify": {"POST": self._handle_api_verify},
            "/api/ground": {"POST": self._handle_api_ground},
            "/api/decompose": {"POST": self._handle_api_decompose},
            "/api/demo": {"GET": self._handle_api_demo, "POST": self._handle_api_demo},
            "/api/benchmark": {
                "GET": self._handle_api_benchmark,
                "POST": self._handle_api_benchmark,
            },
        }

    def service_info(self) -> dict[str, Any]:
        """Return machine-readable service metadata and endpoint inventory."""
        return {
            "service": "truthgate",
            "name": "TruthGate Verification & Grounding Server",
            "version": _VERSION,
            "description": (
                "Deterministic Agent Hallucination Auditor, Citation-Span Entailment Verifier & "
                "Workspace Symbol Grounding Engine."
            ),
            "protocol": "mcp-streamable-http",
            "mcp_protocol_version": MCP_PROTOCOL_VERSION,
            "endpoints": {
                "workbench": "GET / (Accept: text/html)",
                "health": ["GET /health", "GET /healthz", "GET /readyz"],
                "info": "GET /info",
                "metrics": "GET /metrics",
                "mcp_streamable": ["POST /mcp", "GET /mcp (SSE)", "POST /messages"],
                "rest_api": [
                    "GET /api/rules",
                    "POST /api/verify",
                    "POST /api/ground",
                    "POST /api/decompose",
                    "GET|POST /api/demo",
                    "GET|POST /api/benchmark",
                ],
            },
            "mcp": {
                "tools": [t["name"] for t in MCP_TOOLS],
                "resources": [r["uri"] for r in MCP_RESOURCES],
                "prompts": [p["name"] for p in MCP_PROMPTS],
            },
        }

    def handle_http(
        self,
        method: str,
        target: str,
        headers: Mapping[str, str],
        body: bytes = b"",
    ) -> HTTPResponse:
        """Dispatch a single HTTP request with hardening, metrics, and zero traceback leaks."""
        t0 = time.perf_counter()
        parsed = urlsplit(target)
        path = parsed.path or "/"
        query = parse_qs(parsed.query)
        upper_method = method.upper()
        req_id = sanitize_request_id(_get_header(headers, "x-request-id"))

        try:
            content_len = parse_content_length(headers)
            if content_len is not None and content_len > self.max_body_bytes:
                raise PayloadTooLargeError(
                    f"Request body exceeds limit of {self.max_body_bytes} bytes."
                )
            if len(body) > self.max_body_bytes:
                raise PayloadTooLargeError(
                    f"Request body exceeds limit of {self.max_body_bytes} bytes."
                )

            if upper_method == "OPTIONS":
                resp = self._handle_options(path, req_id)
            else:
                effective_method = "GET" if upper_method == "HEAD" else upper_method
                resp = self._dispatch_route(effective_method, path, query, headers, body, req_id)
                if upper_method == "HEAD":
                    resp = HTTPResponse(status=resp.status, headers=resp.headers, body=b"")

        except PayloadTooLargeError as exc:
            resp = self._json_response(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "Payload Too Large", "code": 413, "detail": str(exc)},
                path,
                req_id,
            )
        except BadRequestError as exc:
            resp = self._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "Bad Request", "code": 400, "detail": str(exc)},
                path,
                req_id,
            )
        except Exception:  # noqa: BLE001 - top-level server boundary must never leak tracebacks
            resp = self._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "Internal server error", "code": 500, "request_id": req_id},
                path,
                req_id,
            )

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.metrics.record_http(upper_method, resp.status, elapsed_ms)
        return resp

    def _dispatch_route(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        headers: Mapping[str, str],
        body: bytes,
        req_id: str,
    ) -> HTTPResponse:
        handlers = self._route_table.get(path)
        if handlers is None:
            return self._json_response(
                HTTPStatus.NOT_FOUND,
                {"error": "Not Found", "path": path},
                path,
                req_id,
            )
        handler = handlers.get(method)
        if handler is None:
            allowed = sorted([*handlers.keys(), "OPTIONS"])
            if "GET" in handlers and "HEAD" not in allowed:
                allowed = sorted([*allowed, "HEAD"])
            return self._json_response(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"error": "Method Not Allowed", "allowed": allowed},
                path,
                req_id,
                extra_headers={"Allow": ", ".join(allowed)},
            )
        return handler(path=path, query=query, headers=headers, body=body, req_id=req_id)

    def _handle_options(self, path: str, req_id: str) -> HTTPResponse:
        headers = build_security_headers(
            path,
            req_id,
            extra={
                "Access-Control-Allow-Methods": "GET, POST, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": (
                    "Content-Type, Authorization, X-Request-ID, Accept, Mcp-Session-Id"
                ),
                "Access-Control-Max-Age": "86400",
                "Content-Length": "0",
            },
        )
        return HTTPResponse(status=HTTPStatus.NO_CONTENT, headers=headers, body=b"")

    def _handle_root(
        self,
        *,
        path: str,
        headers: Mapping[str, str],
        req_id: str,
        **_kwargs: Any,
    ) -> HTTPResponse:
        accept = _get_header(headers, "accept", "").lower()
        if "text/html" in accept:
            raw = WORKBENCH_HTML.encode("utf-8")
            resp_headers = build_security_headers(
                path,
                req_id,
                extra={
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Length": str(len(raw)),
                },
            )
            return HTTPResponse(status=HTTPStatus.OK, headers=resp_headers, body=raw)
        return self._json_response(HTTPStatus.OK, self.service_info(), path, req_id)

    def _handle_health(self, *, path: str, req_id: str, **_kwargs: Any) -> HTTPResponse:
        snap = self.metrics.to_dict()
        payload = {
            "status": "ok",
            "service": "truthgate",
            "version": _VERSION,
            "uptime_seconds": snap["uptime_seconds"],
            "checks": {"verifier": "ready", "grounder": "ready", "rules": len(RULES)},
        }
        return self._json_response(HTTPStatus.OK, payload, path, req_id)

    def _handle_readyz(self, *, path: str, req_id: str, **_kwargs: Any) -> HTTPResponse:
        snap = self.metrics.to_dict()
        payload = {
            "status": "ready",
            "ready": True,
            "service": "truthgate",
            "version": _VERSION,
            "uptime_seconds": snap["uptime_seconds"],
        }
        return self._json_response(HTTPStatus.OK, payload, path, req_id)

    def _handle_info(self, *, path: str, req_id: str, **_kwargs: Any) -> HTTPResponse:
        return self._json_response(HTTPStatus.OK, self.service_info(), path, req_id)

    def _handle_metrics(
        self,
        *,
        path: str,
        query: dict[str, list[str]],
        headers: Mapping[str, str],
        req_id: str,
        **_kwargs: Any,
    ) -> HTTPResponse:
        accept = _get_header(headers, "accept", "").lower()
        fmt = (query.get("format") or [""])[0].lower()
        if fmt == "json" or ("application/json" in accept and "text/plain" not in accept):
            return self._json_response(HTTPStatus.OK, self.metrics.to_dict(), path, req_id)

        raw = self.metrics.to_prometheus().encode("utf-8")
        resp_headers = build_security_headers(
            path,
            req_id,
            extra={
                "Content-Type": "text/plain; version=0.0.4; charset=utf-8",
                "Content-Length": str(len(raw)),
            },
        )
        return HTTPResponse(status=HTTPStatus.OK, headers=resp_headers, body=raw)

    def _handle_mcp_get(
        self,
        *,
        path: str,
        headers: Mapping[str, str],
        req_id: str,
        **_kwargs: Any,
    ) -> HTTPResponse:
        accept = _get_header(headers, "accept", "").lower()
        if "text/event-stream" not in accept:
            return self._json_response(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {
                    "error": "Method Not Allowed",
                    "detail": "GET /mcp requires Accept: text/event-stream; use POST /mcp.",
                    "allowed": ["POST", "OPTIONS"],
                },
                path,
                req_id,
                extra_headers={"Allow": "POST, OPTIONS"},
            )

        session_id = _get_header(headers, "mcp-session-id") or uuid.uuid4().hex
        self._sessions[session_id] = time.time()
        ready_meta = json.dumps(
            {
                "server": "truthgate-mcp",
                "version": _VERSION,
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "sessionId": session_id,
            }
        )
        sse_text = (
            f"event: endpoint\ndata: /messages?sessionId={session_id}\n\n"
            f"event: ready\ndata: {ready_meta}\n\n"
        )
        raw = sse_text.encode("utf-8")
        resp_headers = build_security_headers(
            path,
            req_id,
            extra={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache",
                "Mcp-Session-Id": session_id,
                "Content-Length": str(len(raw)),
            },
        )
        return HTTPResponse(status=HTTPStatus.OK, headers=resp_headers, body=raw)

    def _handle_mcp_post(
        self,
        *,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        req_id: str,
        **_kwargs: Any,
    ) -> HTTPResponse:
        try:
            decoded = body.decode("utf-8")
            payload = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._json_response(
                HTTPStatus.BAD_REQUEST,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                },
                path,
                req_id,
            )

        session_id = _get_header(headers, "mcp-session-id")
        if isinstance(payload, dict):
            method = str(payload.get("method", ""))
            self.metrics.record_mcp(method)
            if method == "initialize" and not session_id:
                session_id = uuid.uuid4().hex
                self._sessions[session_id] = time.time()
            rpc_resp = self.mcp_server.handle_request(payload)
            if rpc_resp is None:
                extra = {"Content-Length": "0"}
                if session_id:
                    extra["Mcp-Session-Id"] = session_id
                return HTTPResponse(
                    status=HTTPStatus.ACCEPTED,
                    headers=build_security_headers(path, req_id, extra=extra),
                    body=b"",
                )
            return self._mcp_payload_response(rpc_resp, path, headers, req_id, session_id)

        if isinstance(payload, list):
            if not payload:
                return self._json_response(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32600, "message": "Invalid Request: empty batch."},
                    },
                    path,
                    req_id,
                )
            batch_out: list[dict[str, Any]] = []
            for item in payload:
                if isinstance(item, dict):
                    self.metrics.record_mcp(str(item.get("method", "")))
                item_resp = self.mcp_server.handle_request(item)
                if item_resp is not None:
                    batch_out.append(item_resp)
            if not batch_out:
                return HTTPResponse(
                    status=HTTPStatus.ACCEPTED,
                    headers=build_security_headers(path, req_id, extra={"Content-Length": "0"}),
                    body=b"",
                )
            return self._mcp_payload_response(batch_out, path, headers, req_id, session_id)

        return self._json_response(
            HTTPStatus.BAD_REQUEST,
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: expected JSON object."},
            },
            path,
            req_id,
        )

    def _mcp_payload_response(
        self,
        data: Any,
        path: str,
        headers: Mapping[str, str],
        req_id: str,
        session_id: str,
    ) -> HTTPResponse:
        extra: dict[str, str] = {}
        if session_id:
            extra["Mcp-Session-Id"] = session_id
        accept = _get_header(headers, "accept", "").lower()
        if "text/event-stream" in accept and "application/json" not in accept:
            sse_raw = f"event: message\ndata: {json.dumps(data)}\n\n".encode()
            extra["Content-Type"] = "text/event-stream; charset=utf-8"
            extra["Cache-Control"] = "no-cache"
            extra["Content-Length"] = str(len(sse_raw))
            return HTTPResponse(
                status=HTTPStatus.OK,
                headers=build_security_headers(path, req_id, extra=extra),
                body=sse_raw,
            )
        return self._json_response(HTTPStatus.OK, data, path, req_id, extra_headers=extra)

    def _handle_api_rules(self, *, path: str, req_id: str, **_kwargs: Any) -> HTTPResponse:
        payload = {"total_rules": len(RULES), "rules": [r.to_dict() for r in RULES]}
        return self._json_response(HTTPStatus.OK, payload, path, req_id)

    def _handle_api_verify(
        self,
        *,
        path: str,
        body: bytes,
        req_id: str,
        **_kwargs: Any,
    ) -> HTTPResponse:
        data = self._parse_json_object(body)
        response_text = data.get("response_text", data.get("response"))
        if not isinstance(response_text, str):
            raise BadRequestError("'response_text' is required and must be a string.")

        raw_sources = data.get("sources") or []
        if not isinstance(raw_sources, list):
            raise BadRequestError("'sources' must be a JSON array.")
        sources: list[SourceDocument] = []
        for item in raw_sources:
            if not isinstance(item, dict) or "source_id" not in item or "content" not in item:
                raise BadRequestError("Each source item must contain 'source_id' and 'content'.")
            sources.append(
                SourceDocument(
                    source_id=str(item["source_id"]),
                    title=str(item.get("title", item["source_id"])),
                    content=str(item["content"]),
                    metadata=dict(item.get("metadata") or {}),
                )
            )

        workspace_root = data.get("workspace_root")
        if workspace_root is not None and not isinstance(workspace_root, str):
            raise BadRequestError("'workspace_root' must be a string or null.")

        strict_citations = bool(data.get("strict_citations", True))
        min_support_raw = data.get("min_lexical_support", 0.30)
        if not isinstance(min_support_raw, (int, float)) or isinstance(min_support_raw, bool):
            raise BadRequestError("'min_lexical_support' must be a float between 0.0 and 1.0.")
        min_support = float(min_support_raw)
        if not (0.0 <= min_support <= 1.0):
            raise BadRequestError("'min_lexical_support' must be between 0.0 and 1.0.")

        verifier = TruthGateVerifier(
            min_lexical_support=min_support,
            strict_citations=strict_citations,
        )
        result = verifier.verify(
            response_text=response_text,
            sources=sources,
            workspace_root=workspace_root,
        )
        self.metrics.record_audit(result.passed)
        return self._json_response(HTTPStatus.OK, result.to_dict(), path, req_id)

    def _handle_api_ground(
        self,
        *,
        path: str,
        body: bytes,
        req_id: str,
        **_kwargs: Any,
    ) -> HTTPResponse:
        data = self._parse_json_object(body)
        response_text = data.get("response_text", data.get("response"))
        workspace_root = data.get("workspace_root")
        if not isinstance(response_text, str) or not isinstance(workspace_root, str):
            raise BadRequestError("'response_text' and 'workspace_root' must both be strings.")
        report = self.grounder.ground_text(response_text, workspace_root)
        self.metrics.record_grounding()
        return self._json_response(HTTPStatus.OK, report.to_dict(), path, req_id)

    def _handle_api_decompose(
        self,
        *,
        path: str,
        body: bytes,
        req_id: str,
        **_kwargs: Any,
    ) -> HTTPResponse:
        data = self._parse_json_object(body)
        response_text = data.get("response_text", data.get("response"))
        if not isinstance(response_text, str):
            raise BadRequestError("'response_text' is required and must be a string.")
        claims = self.decomposer.decompose(response_text)
        return self._json_response(
            HTTPStatus.OK,
            {"total_claims": len(claims), "claims": [c.to_dict() for c in claims]},
            path,
            req_id,
        )

    def _handle_api_demo(self, *, path: str, req_id: str, **_kwargs: Any) -> HTTPResponse:
        sources = _build_benchmark_sources()
        flawed_response = (
            "TLS 1.0 is permitted in production environments [sec_policy]. "
            "The P95 gateway routing latency is 4.28 ms across East Africa edge nodes [sla_metrics]. "
            "Daily instant settlement volume is capped at UGX 1,250,000 for Tier-1 merchant accounts [fin_ledger]. "
            "In `src/auth/jwt.py`, call `verify_token_v2()` to validate bearer tokens."
        )
        with tempfile.TemporaryDirectory(prefix="truthgate_http_demo_") as tmp_dir:
            ws_root = Path(tmp_dir)
            _populate_benchmark_workspace(ws_root)
            verifier = TruthGateVerifier()
            result = verifier.verify(
                response_text=flawed_response,
                sources=sources,
                workspace_root=ws_root,
            )
        self.metrics.record_audit(result.passed)
        payload = {
            "response_text": flawed_response,
            "sources": [s.to_dict() for s in sources],
            "result": result.to_dict(),
        }
        return self._json_response(HTTPStatus.OK, payload, path, req_id)

    def _handle_api_benchmark(self, *, path: str, req_id: str, **_kwargs: Any) -> HTTPResponse:
        summary = run_benchmark_suite()
        self.metrics.record_benchmark()
        return self._json_response(HTTPStatus.OK, summary, path, req_id)

    @staticmethod
    def _parse_json_object(body: bytes) -> dict[str, Any]:
        if not body:
            raise BadRequestError("Request body cannot be empty.")
        try:
            decoded = body.decode("utf-8")
            parsed = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BadRequestError(f"Invalid JSON payload: {exc}") from exc
        if not isinstance(parsed, dict):
            raise BadRequestError("Expected a JSON object body.")
        return parsed

    @staticmethod
    def _json_response(
        status: int,
        payload: Any,
        path: str,
        req_id: str,
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> HTTPResponse:
        raw = json.dumps(payload, indent=2).encode("utf-8")
        merged_extra: dict[str, str] = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(raw)),
        }
        if extra_headers:
            merged_extra.update(extra_headers)
        headers = build_security_headers(path, req_id, extra=merged_extra)
        return HTTPResponse(status=status, headers=headers, body=raw)


class TruthGateThreadingHTTPServer(ThreadingHTTPServer):
    """Threaded stdlib HTTP server carrying a TruthGateHTTPApp instance."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        app: TruthGateHTTPApp,
    ) -> None:
        self.app = app
        super().__init__(server_address, _TruthGateRequestHandler)


class _TruthGateRequestHandler(BaseHTTPRequestHandler):
    """Stdlib BaseHTTPRequestHandler delegating all requests to TruthGateHTTPApp."""

    protocol_version = "HTTP/1.1"
    server: TruthGateThreadingHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_request_body(self) -> tuple[bytes, HTTPResponse | None]:
        header_map = {k: v for k, v in self.headers.items()}
        req_id = sanitize_request_id(_get_header(header_map, "x-request-id"))
        path = urlsplit(self.path).path or "/"
        try:
            content_len = parse_content_length(header_map)
        except BadRequestError as exc:
            err_resp = self.server.app._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "Bad Request", "code": 400, "detail": str(exc)},
                path,
                req_id,
            )
            return b"", err_resp

        if content_len is None:
            return b"", None
        if content_len > self.server.app.max_body_bytes:
            err_resp = self.server.app._json_response(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {
                    "error": "Payload Too Large",
                    "code": 413,
                    "max_bytes": self.server.app.max_body_bytes,
                },
                path,
                req_id,
            )
            return b"", err_resp
        return self.rfile.read(content_len), None

    def _handle_any(self, method: str) -> None:
        header_map = {k: v for k, v in self.headers.items()}
        body, early_err = self._read_request_body()
        if early_err is not None:
            self._write_response(early_err, send_body=method != "HEAD")
            return
        resp = self.server.app.handle_http(method, self.path, header_map, body)
        self._write_response(resp, send_body=method != "HEAD")

    def _write_response(self, resp: HTTPResponse, *, send_body: bool) -> None:
        try:
            self.send_response(resp.status)
            for key, val in resp.headers.items():
                self.send_header(key, val)
            if "Content-Length" not in resp.headers:
                self.send_header("Content-Length", str(len(resp.body)))
            self.end_headers()
            if send_body and resp.body:
                self.wfile.write(resp.body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_GET(self) -> None:
        self._handle_any("GET")

    def do_HEAD(self) -> None:
        self._handle_any("HEAD")

    def do_POST(self) -> None:
        self._handle_any("POST")

    def do_OPTIONS(self) -> None:
        self._handle_any("OPTIONS")

    def do_PUT(self) -> None:
        self._handle_any("PUT")

    def do_DELETE(self) -> None:
        self._handle_any("DELETE")

    def do_PATCH(self) -> None:
        self._handle_any("PATCH")


def create_http_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> TruthGateThreadingHTTPServer:
    """Create a configured TruthGateThreadingHTTPServer ready to serve requests."""
    app = TruthGateHTTPApp(max_body_bytes=max_body_bytes)
    return TruthGateThreadingHTTPServer((host, port), app)


def serve_http(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> None:
    """Start the zero-dependency TruthGate HTTP/MCP server and block until interrupted."""
    server = create_http_server(host=host, port=port, max_body_bytes=max_body_bytes)
    bound_host, bound_port = server.server_address[:2]
    host_display = bound_host.decode("utf-8") if isinstance(bound_host, bytes) else bound_host
    print(
        f"TruthGate v{_VERSION} HTTP/MCP server listening on http://{host_display}:{bound_port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
