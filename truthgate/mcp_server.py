"""JSON-RPC 2.0 Model Context Protocol (MCP) Server for TruthGate."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO

from truthgate.benchmark import get_benchmark_scenarios, run_benchmark_suite
from truthgate.corrector import TruthGateVerifier
from truthgate.grounding import WorkspaceSymbolGrounder
from truthgate.models import RULES, SourceDocument

MCP_PROTOCOL_VERSION = "2024-11-05"

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "truthgate_verify_response",
        "description": (
            "Verify an AI agent response against source documents and/or a workspace directory "
            "across deterministic rules TG001-TG008, returning claim-by-claim verdicts and a "
            "surgical self-correction prompt when any check fails."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "response_text": {
                    "type": "string",
                    "description": "The full markdown response produced by the AI agent.",
                },
                "sources": {
                    "type": "array",
                    "description": "List of grounding context documents ({source_id, title, content}).",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source_id": {"type": "string"},
                            "title": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["source_id", "content"],
                    },
                },
                "workspace_root": {
                    "type": "string",
                    "description": (
                        "Optional workspace directory path for verifying file paths, "
                        "#Lstart-Lend ranges, and AST symbols."
                    ),
                },
                "strict_citations": {
                    "type": "boolean",
                    "description": "Whether uncited factual claims trigger TG005 (default: true).",
                    "default": True,
                },
            },
            "required": ["response_text"],
        },
    },
    {
        "name": "truthgate_ground_workspace_refs",
        "description": (
            "Verify all file paths, #Lstart-Lend line ranges, and AST symbols referenced in "
            "markdown text against a workspace directory (rules TG006-TG008)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "response_text": {
                    "type": "string",
                    "description": "Markdown text containing file links, line ranges, or symbol references.",
                },
                "workspace_root": {
                    "type": "string",
                    "description": "Absolute or relative path to the workspace directory.",
                },
            },
            "required": ["response_text", "workspace_root"],
        },
    },
    {
        "name": "truthgate_list_rules",
        "description": "List all 8 deterministic verification rules (TG001-TG008) enforced by TruthGate.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "truthgate_run_benchmark",
        "description": "Run the reproducible 40-scenario TruthGate hallucination & grounding benchmark suite.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

MCP_RESOURCES: list[dict[str, Any]] = [
    {
        "uri": "truthgate://rules",
        "name": "TruthGate Verification Rules (TG001-TG008)",
        "description": "Canonical catalog of all 8 deterministic entailment and AST grounding rules.",
        "mimeType": "application/json",
    },
    {
        "uri": "truthgate://benchmark/scenarios",
        "name": "TruthGate 40-Scenario Benchmark Catalog",
        "description": "All 10 grounded truthful and 30 adversarial hallucination scenarios.",
        "mimeType": "application/json",
    },
    {
        "uri": "truthgate://docs/architecture",
        "name": "TruthGate Architecture Specification",
        "description": "Deterministic claim decomposition, entailment verification, and surgical repair flow.",
        "mimeType": "text/markdown",
    },
]

MCP_PROMPTS: list[dict[str, Any]] = [
    {
        "name": "audit-response",
        "description": (
            "Audit an AI agent response against source documents and optional workspace symbols "
            "using TruthGate rules TG001-TG008."
        ),
        "arguments": [
            {
                "name": "response_text",
                "description": "Markdown response produced by the AI agent.",
                "required": True,
            },
            {
                "name": "workspace_root",
                "description": "Optional workspace directory for code symbol grounding.",
                "required": False,
            },
        ],
    },
    {
        "name": "ground-workspace",
        "description": (
            "Verify every file path, #Lstart-Lend line range, and AST symbol in a coding agent "
            "response against a repository workspace."
        ),
        "arguments": [
            {
                "name": "response_text",
                "description": "Response text containing code references.",
                "required": True,
            },
            {
                "name": "workspace_root",
                "description": "Path to the repository workspace root.",
                "required": True,
            },
        ],
    },
    {
        "name": "surgical-repair",
        "description": "Structure a minimal rewrite prompt to fix detected TruthGate violations.",
        "arguments": [
            {
                "name": "violation_codes",
                "description": "Comma-separated violated rule codes (e.g. TG003,TG004).",
                "required": True,
            },
            {
                "name": "offending_claim",
                "description": "The offending claim text requiring correction.",
                "required": True,
            },
        ],
    },
]

_FALLBACK_ARCH_DOC = """# TruthGate Deterministic Verification Pipeline

1. **ClaimDecomposer**: Splits markdown responses into atomic propositions while extracting citations (`[doc_id]`), normalized numbers/currencies/versions, explicit negations/antonyms, and code references (`path#Lstart-Lend`, backtick symbols).
2. **CitationEntailmentVerifier (`TG001`-`TG005`)**: Checks phantom citations, IDF-weighted lexical/entity support, negation & antonym polarity inversions, numeric & magnitude drift, and uncited factual assertions.
3. **WorkspaceSymbolGrounder (`TG006`-`TG008`)**: Enforces path-traversal confinement and verifies file existence, line-range bounds, and Python AST / regex symbol presence.
4. **SurgicalRepairCompiler**: Compiles compact, deterministic self-correction prompts preserving already-verified claims.
"""


class TruthGateMCPServer:
    """JSON-RPC 2.0 MCP server for deterministic agent verification and AST grounding."""

    def __init__(self) -> None:
        self.grounder = WorkspaceSymbolGrounder()

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Process a single JSON-RPC 2.0 request dictionary and return a response dictionary."""
        if not isinstance(request, dict):
            return self._error(None, -32600, "Invalid Request: expected JSON object.")

        method = request.get("method", "")
        if not isinstance(method, str):
            return self._error(request.get("id"), -32600, "Invalid Request: 'method' must be a string.")
        req_id = request.get("id")
        raw_params = request.get("params")
        if raw_params is None:
            params: dict[str, Any] = {}
        elif isinstance(raw_params, dict):
            params = raw_params
        else:
            return self._error(req_id, -32602, "Invalid params: expected JSON object.")

        # Notifications have no id and expect no response
        if req_id is None and (method.startswith("notifications/") or method == "initialized"):
            return None

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "serverInfo": {
                        "name": "truthgate-mcp",
                        "version": "1.0.0",
                    },
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                        "prompts": {"listChanged": False},
                    },
                }
                return self._ok(req_id, result)

            if method == "ping":
                return self._ok(req_id, {})

            if method == "tools/list":
                return self._ok(req_id, {"tools": MCP_TOOLS})

            if method == "tools/call":
                tool_name = params.get("name", "")
                if not isinstance(tool_name, str) or not tool_name:
                    raise ValueError("'name' is required and must be a non-empty string.")
                raw_args = params.get("arguments")
                if raw_args is None:
                    arguments: dict[str, Any] = {}
                elif isinstance(raw_args, dict):
                    arguments = raw_args
                else:
                    raise ValueError("'arguments' must be a JSON object.")
                tool_output = self._call_tool(tool_name, arguments)
                return self._ok(
                    req_id,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(tool_output, indent=2),
                            }
                        ],
                        "isError": False,
                    },
                )

            if method == "resources/list":
                return self._ok(req_id, {"resources": MCP_RESOURCES})

            if method == "resources/read":
                uri = params.get("uri", "")
                if not isinstance(uri, str) or not uri:
                    raise ValueError("'uri' is required and must be a non-empty string.")
                content = self._read_resource(uri)
                if content is None:
                    return self._error(req_id, -32002, f"Resource not found: {uri}")
                return self._ok(req_id, {"contents": [content]})

            if method == "prompts/list":
                return self._ok(req_id, {"prompts": MCP_PROMPTS})

            if method == "prompts/get":
                prompt_name = params.get("name", "")
                if not isinstance(prompt_name, str) or not prompt_name:
                    raise ValueError("'name' is required and must be a non-empty string.")
                raw_prompt_args = params.get("arguments")
                if raw_prompt_args is None:
                    prompt_args: dict[str, Any] = {}
                elif isinstance(raw_prompt_args, dict):
                    prompt_args = raw_prompt_args
                else:
                    raise ValueError("'arguments' must be a JSON object.")
                prompt_payload = self._get_prompt(prompt_name, prompt_args)
                return self._ok(req_id, prompt_payload)

            return self._error(req_id, -32601, f"Method not found: {method}")

        except (ValueError, KeyError, TypeError, OSError) as exc:
            return self._error(req_id, -32602, str(exc))

    def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "truthgate_verify_response":
            response_text = arguments.get("response_text")
            if not isinstance(response_text, str):
                raise ValueError("'response_text' is required and must be a string.")
            raw_sources = arguments.get("sources") or []
            if not isinstance(raw_sources, list):
                raise ValueError("'sources' must be a list of source objects.")
            sources = [
                SourceDocument(
                    source_id=str(s["source_id"]),
                    title=str(s.get("title", s["source_id"])),
                    content=str(s["content"]),
                    metadata=dict(s.get("metadata") or {}),
                )
                for s in raw_sources
            ]
            workspace_root = arguments.get("workspace_root")
            strict_citations = bool(arguments.get("strict_citations", True))
            verifier = TruthGateVerifier(strict_citations=strict_citations)
            res = verifier.verify(
                response_text=response_text,
                sources=sources,
                workspace_root=workspace_root,
            )
            return res.to_dict()

        if tool_name == "truthgate_ground_workspace_refs":
            response_text = arguments.get("response_text")
            workspace_root = arguments.get("workspace_root")
            if not isinstance(response_text, str) or not isinstance(workspace_root, str):
                raise ValueError("Both 'response_text' and 'workspace_root' must be strings.")
            report = self.grounder.ground_text(response_text, workspace_root)
            return report.to_dict()

        if tool_name == "truthgate_list_rules":
            return {
                "total_rules": len(RULES),
                "rules": [r.to_dict() for r in RULES],
            }

        if tool_name == "truthgate_run_benchmark":
            return run_benchmark_suite()

        raise ValueError(f"Unknown tool: {tool_name}")

    def _read_resource(self, uri: str) -> dict[str, Any] | None:
        if uri == "truthgate://rules":
            payload = {
                "total_rules": len(RULES),
                "rules": [r.to_dict() for r in RULES],
            }
            return {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(payload, indent=2),
            }

        if uri == "truthgate://benchmark/scenarios":
            scenarios = [
                {
                    "scenario_id": s.scenario_id,
                    "category": s.category,
                    "expected_passed": s.expected_passed,
                    "expected_rule": s.expected_rule,
                    "use_workspace": s.use_workspace,
                    "response_text": s.response_text,
                    "description": s.description,
                }
                for s in get_benchmark_scenarios()
            ]
            return {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(
                    {"total_scenarios": len(scenarios), "scenarios": scenarios},
                    indent=2,
                ),
            }

        if uri == "truthgate://docs/architecture":
            arch_path = Path(__file__).resolve().parent.parent / "docs" / "architecture.md"
            text = arch_path.read_text(encoding="utf-8") if arch_path.exists() else _FALLBACK_ARCH_DOC
            return {
                "uri": uri,
                "mimeType": "text/markdown",
                "text": text,
            }

        return None

    def _get_prompt(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "audit-response":
            response_text = arguments.get("response_text")
            if not isinstance(response_text, str) or not response_text.strip():
                raise ValueError("Prompt 'audit-response' requires non-empty 'response_text'.")
            workspace_root = arguments.get("workspace_root")
            ws_note = f"\nWorkspace root to ground against: `{workspace_root}`" if workspace_root else ""
            prompt_text = (
                "Call `truthgate_verify_response` on the following agent response using all "
                "available RAG/tool `sources`"
                f"{ws_note}.\n\n"
                "If any TG001-TG008 violation is returned, apply ONLY the fixes specified in "
                "`repair_prompt` and re-verify until `passed == true`.\n\n"
                f"Response under audit:\n{response_text}"
            )
            return {
                "description": "Audit an agent response with TruthGate TG001-TG008.",
                "messages": [
                    {"role": "user", "content": {"type": "text", "text": prompt_text}},
                ],
            }

        if name == "ground-workspace":
            response_text = arguments.get("response_text")
            workspace_root = arguments.get("workspace_root")
            if (
                not isinstance(response_text, str)
                or not response_text.strip()
                or not isinstance(workspace_root, str)
                or not workspace_root.strip()
            ):
                raise ValueError(
                    "Prompt 'ground-workspace' requires non-empty 'response_text' and 'workspace_root'."
                )
            prompt_text = (
                f"Call `truthgate_ground_workspace_refs` with `workspace_root={workspace_root!r}` "
                "to validate every file path (TG006), `#Lstart-Lend` line range (TG007), and AST "
                "symbol (TG008) before presenting this response:\n\n"
                f"{response_text}"
            )
            return {
                "description": "Verify workspace paths, line ranges, and AST symbols.",
                "messages": [
                    {"role": "user", "content": {"type": "text", "text": prompt_text}},
                ],
            }

        if name == "surgical-repair":
            violation_codes = arguments.get("violation_codes")
            offending_claim = arguments.get("offending_claim")
            if (
                not isinstance(violation_codes, str)
                or not violation_codes.strip()
                or not isinstance(offending_claim, str)
                or not offending_claim.strip()
            ):
                raise ValueError(
                    "Prompt 'surgical-repair' requires 'violation_codes' and 'offending_claim'."
                )
            prompt_text = (
                f"TruthGate detected deterministic violation(s) [{violation_codes}] in:\n"
                f'"{offending_claim}"\n\n'
                "Rewrite ONLY this claim so its citations, polarity, numeric figures, and "
                "workspace symbols match the ground-truth context verbatim, preserving all "
                "already-verified claims untouched."
            )
            return {
                "description": "Surgical hallucination repair instructions.",
                "messages": [
                    {"role": "user", "content": {"type": "text", "text": prompt_text}},
                ],
            }

        raise ValueError(f"Unknown prompt: {name}")

    @staticmethod
    def _ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result,
        }

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": code,
                "message": message,
            },
        }

    def serve_stdio(
        self,
        stdin: TextIO = sys.stdin,
        stdout: TextIO = sys.stdout,
    ) -> None:
        """Run the JSON-RPC 2.0 server loop over stdin/stdout."""
        for line in stdin:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                req = json.loads(stripped)
            except json.JSONDecodeError as exc:
                parse_err = self._error(None, -32700, f"Parse error: {exc}")
                stdout.write(json.dumps(parse_err) + "\n")
                stdout.flush()
                continue

            resp: dict[str, Any] | None = self.handle_request(req)
            if resp is not None:
                stdout.write(json.dumps(resp) + "\n")
                stdout.flush()
