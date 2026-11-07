"""Unit tests for truthgate.models dataclasses, rules catalog, and validation."""

from __future__ import annotations

import pytest

from truthgate.models import (
    RULE_MAP,
    RULES,
    AtomicClaim,
    ClaimVerdict,
    CodeReference,
    GroundingReport,
    NumericToken,
    SourceDocument,
    VerdictStatus,
    VerificationResult,
)


def test_rules_catalog_has_all_eight_rules() -> None:
    assert len(RULES) == 8
    expected_codes = [f"TG00{i}" for i in range(1, 9)]
    assert [r.code for r in RULES] == expected_codes
    for code in expected_codes:
        assert code in RULE_MAP
        d = RULE_MAP[code].to_dict()
        assert d["code"] == code
        assert d["name"]
        assert d["description"]


def test_source_document_validation_and_to_dict() -> None:
    doc = SourceDocument(
        source_id="doc_1",
        title="Security Spec",
        content="TLS 1.3 is mandatory.",
        metadata={"tier": "prod"},
    )
    assert doc.to_dict()["source_id"] == "doc_1"
    assert doc.to_dict()["metadata"]["tier"] == "prod"

    with pytest.raises(ValueError, match="source_id"):
        SourceDocument(source_id="   ", title="Bad", content="text")

    with pytest.raises(ValueError, match="content"):
        SourceDocument(source_id="doc_2", title="Bad", content=None)  # type: ignore[arg-type]


def test_numeric_token_matching_and_units() -> None:
    t1 = NumericToken(raw="42.8 ms", value=42.8, unit="ms")
    t2 = NumericToken(raw="42.80 ms", value=42.8, unit="ms")
    t3 = NumericToken(raw="4.28 ms", value=4.28, unit="ms")
    t4 = NumericToken(raw="42.8 s", value=42.8, unit="s")
    v1 = NumericToken(raw="TLS 1.3", value=1.3, unit="version", is_version=True)
    v2 = NumericToken(raw="TLS 1.0", value=1.0, unit="version", is_version=True)

    assert t1.matches(t2)
    assert not t1.matches(t3)
    assert not t1.matches(t4)
    assert not t1.matches(v1)
    assert not v1.matches(v2)
    assert v1.matches(NumericToken(raw="TLS 1.3", value=1.3, unit="version", is_version=True))
    assert t1.to_dict()["value"] == 42.8


def test_atomic_claim_and_code_reference_serialization() -> None:
    ref = CodeReference(
        raw="src/auth/jwt.py#L10-L20",
        file_path="src/auth/jwt.py",
        start_line=10,
        end_line=20,
        symbol="verify_token",
    )
    claim = AtomicClaim(
        claim_id="claim_1",
        raw_text="Verify token in src/auth/jwt.py#L10-L20 [doc_1].",
        cited_sources=["doc_1"],
        numbers=[NumericToken(raw="10", value=10.0)],
        has_negation=False,
        entities=["verify", "token"],
        code_refs=[ref],
    )
    data = claim.to_dict()
    assert data["claim_id"] == "claim_1"
    assert data["code_refs"][0]["file_path"] == "src/auth/jwt.py"

    with pytest.raises(ValueError, match="claim_id"):
        AtomicClaim(claim_id="", raw_text="invalid")


def test_claim_verdict_validation_and_properties() -> None:
    v_ok = ClaimVerdict(
        claim_id="claim_1",
        claim_text="Grounded claim [doc_1].",
        status=VerdictStatus.SUPPORTED.value,
        confidence=0.95,
        explanation="Supported.",
    )
    assert v_ok.passed is True
    assert v_ok.to_dict()["passed"] is True

    v_fail = ClaimVerdict(
        claim_id="claim_2",
        claim_text="Drifted claim [doc_1].",
        status=VerdictStatus.NUMERIC_DRIFT.value,
        confidence=0.80,
        explanation="Drift detected.",
        violation_code="TG004",
    )
    assert v_fail.passed is False

    with pytest.raises(ValueError, match="Invalid ClaimVerdict.status"):
        ClaimVerdict(
            claim_id="c1",
            claim_text="x",
            status="UNKNOWN_STATUS",
            confidence=0.5,
            explanation="bad",
        )

    with pytest.raises(ValueError, match="confidence"):
        ClaimVerdict(
            claim_id="c1",
            claim_text="x",
            status=VerdictStatus.SUPPORTED.value,
            confidence=1.5,
            explanation="bad",
        )


def test_grounding_report_and_verification_result_serialization() -> None:
    gr = GroundingReport(
        workspace_root="/tmp/ws",
        total_references=2,
        valid_references=2,
        broken_references=0,
    )
    assert gr.passed is True

    vr = VerificationResult(
        passed=True,
        overall_score=1.0,
        total_claims=1,
        supported_claims=1,
        failed_claims=0,
        violation_counts={},
        claim_verdicts=[],
        grounding_report=gr,
        repair_prompt="",
        elapsed_ms=0.1234,
    )
    d = vr.to_dict()
    assert d["passed"] is True
    assert d["grounding_report"]["passed"] is True
