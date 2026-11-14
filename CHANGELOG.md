# Changelog

All notable changes to `truthgate` are documented in this file.

## [1.1.0] - 2026-11-14

### Added
- Dedicated Warm Ivory multi-page static website (`website/`, live at `https://codesphere-u.github.io/truthgate/`) with Azure Verification Blue (`#38bdf8`) and Emerald Entailment (`#34d399`) accents.
- Browser-local Interactive Studio (`website/studio.html`) with live citation entailment auditor, surgical repair compiler, workspace AST symbol simulator, and MCP config generator.
- Verified 40-scenario benchmark proof page (`website/proof.html`), filterable `TG001`–`TG008` rules explorer (`website/rules.html`), and sticky-rail documentation (`website/docs.html`).
- Machine discovery endpoints (`llms.txt`, `llms-full.txt`, `sitemap.xml`, `robots.txt`), Schema.org `@graph` JSON-LD, repository guides (`docs/`), GitHub Pages workflow (`.github/workflows/pages.yml`), and automated website tests (`tests/test_website_and_docs.py`).

### Changed
- Bumped package, CLI, MCP server, and HTTP workbench version to `1.1.0`.

## [1.0.0] - 2026-11-07

### Added
- **`truthgate/models.py`**: Validated dataclasses (`SourceDocument`, `NumericToken`, `CodeReference`, `AtomicClaim`, `ClaimVerdict`, `GroundingReport`, `VerificationResult`, `RuleDefinition`).
- **`truthgate/decomposer.py` (`ClaimDecomposer`)**: Atomic sentence splitter and extractor for inline citations (`[doc_1]`, `[source:id]`, `(ref: id)`), numeric & currency tokens (`42.8 ms`, `99.95%`, `$1,250.00`, `UGX 50,000`), negation markers, technical entities, and workspace file/line/symbol references.
- **`truthgate/entailment.py` (`CitationEntailmentVerifier`)**: Deterministic citation-span entailment engine implementing `TG001` (Phantom Source Citation), `TG002` (Low Lexical/Entity Support), `TG003` (Negation & Antonym Polarity Inversion), `TG004` (Numeric & Magnitude Drift), and `TG005` (Uncited Factual Assertion).
- **`truthgate/grounding.py` (`WorkspaceSymbolGrounder`)**: AST & filesystem workspace grounding engine implementing `TG006` (Non-Existent File Path), `TG007` (Out-of-Bounds Line Range), and `TG008` (Phantom Code Symbol across Python AST and TS/JS/Go/Rust symbol tables).
- **`truthgate/corrector.py` (`SurgicalRepairCompiler`)**: Token-efficient self-correction prompt compiler citing exact violation codes (`TG001`–`TG008`) and verbatim ground-truth spans.
- **`truthgate/benchmark.py` (`run_benchmark_suite`)**: Reproducible 40-scenario benchmark suite (10 grounded truthful scenarios + 30 adversarial hallucination scenarios across `TG001`–`TG008`).
- **`truthgate/mcp_server.py`**: JSON-RPC 2.0 Stdio MCP server exposing `truthgate_verify_response`, `truthgate_ground_workspace_refs`, `truthgate_list_rules`, and `truthgate_run_benchmark`.
- **`truthgate/cli.py`**: Full CLI supporting `truthgate demo`, `truthgate verify`, `truthgate ground`, `truthgate benchmark`, `truthgate rules`, and `truthgate mcp`.
