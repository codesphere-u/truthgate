"""Unit tests for truthgate.grounding (WorkspaceSymbolGrounder rules TG006-TG008)."""

from __future__ import annotations

from pathlib import Path

from truthgate.benchmark import _populate_benchmark_workspace
from truthgate.grounding import WorkspaceSymbolGrounder
from truthgate.models import VerdictStatus


def test_workspace_grounding_valid_references(tmp_path: Path) -> None:
    _populate_benchmark_workspace(tmp_path)
    grounder = WorkspaceSymbolGrounder()
    report = grounder.ground_text(
        "Token validation is in [jwt.py](src/auth/jwt.py#L8-L19) using `TokenValidator.verify_token`.",
        tmp_path,
    )
    assert report.passed is True
    assert report.broken_references == 0
    assert report.valid_references >= 1


def test_tg006_non_existent_file_and_path_traversal(tmp_path: Path) -> None:
    _populate_benchmark_workspace(tmp_path)
    grounder = WorkspaceSymbolGrounder()

    r1 = grounder.ground_text("See `src/auth/non_existent.py` for details.", tmp_path)
    assert r1.passed is False
    assert r1.verdicts[0].violation_code == "TG006"
    assert r1.verdicts[0].status == VerdictStatus.BROKEN_CODE_REF.value

    r2 = grounder.ground_text("See [passwd](../../etc/passwd.py#L1-L5) for details.", tmp_path)
    assert r2.passed is False
    assert r2.verdicts[0].violation_code == "TG006"


def test_tg007_out_of_bounds_line_range(tmp_path: Path) -> None:
    _populate_benchmark_workspace(tmp_path)
    grounder = WorkspaceSymbolGrounder()

    report = grounder.ground_text(
        "Check [src/auth/jwt.py#L100-L140] for the token decoder.",
        tmp_path,
    )
    assert report.passed is False
    assert report.verdicts[0].violation_code == "TG007"


def test_tg008_phantom_code_symbol_python_and_ts(tmp_path: Path) -> None:
    _populate_benchmark_workspace(tmp_path)
    grounder = WorkspaceSymbolGrounder()

    r_py = grounder.ground_text(
        "In `src/auth/jwt.py`, call `verify_token_v2()` to authenticate.",
        tmp_path,
    )
    assert r_py.passed is False
    assert any(v.violation_code == "TG008" for v in r_py.verdicts)

    r_ts = grounder.ground_text(
        "In `src/gateway/router.ts`, invoke `hallucinatedMiddleware()`.",
        tmp_path,
    )
    assert r_ts.passed is False
    assert any(v.violation_code == "TG008" for v in r_ts.verdicts)


def test_go_and_rust_symbol_extraction(tmp_path: Path) -> None:
    go_file = tmp_path / "main.go"
    go_file.write_text(
        "package main\ntype WorkerPool struct {}\nfunc DispatchJob() bool { return true }\n",
        encoding="utf-8",
    )
    rs_file = tmp_path / "lib.rs"
    rs_file.write_text(
        "pub struct AuditChain {}\npub async fn verify_merkle_root() -> bool { true }\n",
        encoding="utf-8",
    )
    grounder = WorkspaceSymbolGrounder()
    r_ok = grounder.ground_text(
        "In `main.go`, call `DispatchJob()`. In `lib.rs`, call `verify_merkle_root()`.",
        tmp_path,
    )
    assert r_ok.passed is True

    r_bad = grounder.ground_text(
        "In `lib.rs`, call `phantom_rust_fn()`.",
        tmp_path,
    )
    assert r_bad.passed is False
    assert r_bad.verdicts[0].violation_code == "TG008"


def test_standalone_symbol_verified_against_workspace_index(tmp_path: Path) -> None:
    _populate_benchmark_workspace(tmp_path)
    grounder = WorkspaceSymbolGrounder()
    r_ok = grounder.ground_text("Call function `decode_payload()` to parse claims.", tmp_path)
    assert r_ok.passed is True

    r_bad = grounder.ground_text("Call function `non_existent_global_helper()` now.", tmp_path)
    assert r_bad.passed is False
    assert r_bad.verdicts[0].violation_code == "TG008"

