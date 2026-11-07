# Contributing to TruthGate

Thank you for your interest in contributing to **TruthGate** (`codesphere-u/truthgate`)!

## Development Setup

```bash
git clone https://github.com/codesphere-u/truthgate.git
cd truthgate
python3 -m pip install -e ".[dev]"
pytest -v
```

## Adding or Tuning Verification Rules

1. Every verification rule must have a unique deterministic code (`TG001`–`TG008` or subsequent `TG00X` identifiers) documented in `truthgate/models.py`.
2. Any change to `ClaimDecomposer`, `CitationEntailmentVerifier`, or `WorkspaceSymbolGrounder` must maintain **0.0% False Positive Rate** on the grounded benchmark corpus (`truthgate benchmark`).
3. Include unit tests in `tests/` covering both positive (truthful) and adversarial (hallucinated) examples.
4. Ensure all tests and CLI smoke checks pass before opening a pull request:
   ```bash
   pytest -v
   truthgate demo
   truthgate benchmark
   ```
