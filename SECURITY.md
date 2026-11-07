# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |

## Security Architecture

`truthgate` is designed for safe, deterministic runtime execution inside AI agent verification pipelines:

- **Zero External Runtime Dependencies:** Uses only the Python 3.11+ standard library (`ast`, `re`, `math`, `json`, `pathlib`, `dataclasses`).
- **Static AST Parsing Only:** Code symbol grounding uses `ast.parse()` without ever importing or executing untrusted workspace files.
- **Workspace Path Traversal Protection:** `WorkspaceSymbolGrounder` resolves candidate file paths and blocks path traversal attempts (`..`) outside the configured workspace root.
- **No Network Calls:** All verification rules (`TG001`–`TG008`) execute locally and deterministically in sub-millisecond latency.

## Reporting a Vulnerability

Please report security vulnerabilities to **codesphereinnovationz@gmail.com** with the subject line `[SECURITY] truthgate`. We acknowledge reports within 48 hours and provide a remediation timeline within 5 business days.
