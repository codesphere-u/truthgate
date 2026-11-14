# MCP Server & HTTP Developer Workbench Guide

## Stdio MCP (`truthgate mcp`)

Expose `truthgate_verify_response`, `truthgate_ground_workspace_refs`, `truthgate_list_rules`, and `truthgate_run_benchmark` over JSON-RPC 2.0 Stdio:

```json
{
  "mcpServers": {
    "truthgate": {
      "command": "truthgate",
      "args": ["mcp"]
    }
  }
}
```

## Streamable HTTP / SSE MCP & Workbench (`truthgate serve-http`)

```bash
truthgate serve-http --host 127.0.0.1 --port 8000
```

- `GET /`: Interactive Workbench UI (`Accept: text/html`) or JSON metadata
- `GET` / `HEAD /health`, `/healthz`, `/readyz`, `/metrics`
- `POST /mcp`, `GET /mcp` (SSE), `POST /messages`
- `GET /api/rules`, `GET /api/demo`, `GET /api/benchmark` & `POST /api/verify`, `/api/ground`, `/api/decompose`
