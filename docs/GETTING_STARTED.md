# Getting Started with TruthGate

TruthGate provides deterministic citation-span entailment (`TG001`–`TG005`) and workspace AST symbol grounding (`TG006`–`TG008`) in `0.128 ms` P50 with zero core runtime dependencies on Python 3.11+.

## Installation

```bash
pip install git+https://github.com/codesphere-u/truthgate.git
# Or with FastAPI ASGI extras:
pip install -e ".[server]"
```

## Quick CLI Commands

```bash
truthgate demo
truthgate benchmark
truthgate rules
truthgate verify --response "Claim [doc_1]." --sources-json sources.json --workspace . --fail-on-violation
```

Explore the interactive browser simulator at [https://codesphere-u.github.io/truthgate/studio.html](https://codesphere-u.github.io/truthgate/studio.html).
