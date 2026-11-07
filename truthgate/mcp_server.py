"""JSON-RPC 2.0 Model Context Protocol (MCP) Stdio Server for TruthGate."""

from __future__ import annotations

import json
import sys
from typing import Any

from truthgate.benchmark import run_benchmark_suite
from truthgate.corrector import TruthGateVerifier
from truthgate.grounding import WorkspaceSymbolGrounder
from truthgate.models import RULES, SourceDocument

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
                    "description": "Optional workspace directory path for verifying file paths, #Lstart-Lend ranges, and AST symbols.",
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


class TruthGateMCPServer:
    """JSON-RPC 2.0 MCP server for deterministic agent verification and AST grounding."""

    def __init__(self) -> None:
        self.grounder = WorkspaceSymbolGrounder()

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Process a single JSON-RPC 2.0 request dictionary and return a response dictionary."""
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params") or {}

        # Notifications have no id and expect no response
        if req_id is None and method.startswith("notifications/"):
            return None

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {
                        "name": "truthgate-mcp",
                        "version": "1.0.0",
                    },
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
                }
                return self._ok(req_id, result)

            if method == "ping":
                return self._ok(req_id, {})

            if method == "tools/list":
                return self._ok(req_id, {"tools": MCP_TOOLS})

            if method == "tools/call":
                tool_name = params.get("name", "")
                arguments = params.get("arguments") or {}
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

            return self._error(req_id, -32601, f"Method not found: {method}")

        except Exception as exc:
            return self._error(req_id, -32602, str(exc))

    def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "truthgate_verify_response":
            response_text = arguments.get("response_text")
            if not isinstance(response_text, str):
                raise ValueError("'response_text' is required and must be a string.")
            raw_sources = arguments.get("sources") or []
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

    def serve_stdio(self) -> None:
        """Run the JSON-RPC 2.0 server loop over stdin/stdout."""
        for line in sys.stdin:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                req = json.loads(stripped)
            except json.JSONDecodeError as exc:
                resp = self._error(None, -32700, f"Parse error: {exc}")
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
                continue

            resp = self.handle_request(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
