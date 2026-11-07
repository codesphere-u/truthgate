"""Unit tests for truthgate.entailment (CitationEntailmentVerifier rules TG001-TG005)."""

from __future__ import annotations

from truthgate.decomposer import ClaimDecomposer
from truthgate.entailment import CitationEntailmentVerifier
from truthgate.models import SourceDocument, VerdictStatus


def _sample_sources() -> list[SourceDocument]:
    return [
        SourceDocument(
            source_id="sec_1",
            title="Security Policy",
            content=(
                "TLS 1.0 is not permitted in production environments. "
                "Strict mTLS validation is disabled by default on staging healthchecks. "
                "Backups are encrypted at rest with AES-256."
            ),
        ),
        SourceDocument(
            source_id="perf_1",
            title="Performance SLA",
            content="Gateway P95 latency is 42.8 ms with 99.95% availability and UGX 1,250,000 daily limit.",
        ),
    ]


def test_tg001_phantom_citation_detected() -> None:
    decomposer = ClaimDecomposer()
    verifier = CitationEntailmentVerifier(decomposer=decomposer)
    claims = decomposer.decompose("Gateway P95 latency is 42.8 ms [missing_doc].")
    verdicts = verifier.verify_claims(claims, _sample_sources())
    assert len(verdicts) == 1
    assert verdicts[0].violation_code == "TG001"
    assert verdicts[0].status == VerdictStatus.PHANTOM_CITATION.value


def test_tg002_low_lexical_entity_support_detected() -> None:
    decomposer = ClaimDecomposer()
    verifier = CitationEntailmentVerifier(decomposer=decomposer)
    claims = decomposer.decompose(
        "Kubernetes horizontal pod autoscaler schedules CUDA GPU workloads [sec_1]."
    )
    verdicts = verifier.verify_claims(claims, _sample_sources())
    assert verdicts[0].violation_code == "TG002"
    assert verdicts[0].status == VerdictStatus.UNVERIFIED.value


def test_tg003_negation_and_antonym_polarity_inversion() -> None:
    decomposer = ClaimDecomposer()
    verifier = CitationEntailmentVerifier(decomposer=decomposer)

    # Explicit 'not' dropped
    c1 = decomposer.decompose("TLS 1.0 is permitted in production environments [sec_1].")
    v1 = verifier.verify_claims(c1, _sample_sources())[0]
    assert v1.violation_code == "TG003"
    assert v1.status == VerdictStatus.CONTRADICTED.value

    # Antonym 'disabled' -> 'enabled'
    c2 = decomposer.decompose(
        "Strict mTLS validation is enabled by default on staging healthchecks [sec_1]."
    )
    v2 = verifier.verify_claims(c2, _sample_sources())[0]
    assert v2.violation_code == "TG003"
    assert v2.status == VerdictStatus.CONTRADICTED.value

    # Double-negative equivalence: "not enabled" matches "disabled"
    c3 = decomposer.decompose(
        "Strict mTLS validation is not enabled by default on staging healthchecks [sec_1]."
    )
    v3 = verifier.verify_claims(c3, _sample_sources())[0]
    assert v3.passed is True


def test_tg004_numeric_and_currency_drift() -> None:
    decomposer = ClaimDecomposer()
    verifier = CitationEntailmentVerifier(decomposer=decomposer)

    c_lat = decomposer.decompose("Gateway P95 latency is 4.28 ms [perf_1].")
    v_lat = verifier.verify_claims(c_lat, _sample_sources())[0]
    assert v_lat.violation_code == "TG004"
    assert v_lat.status == VerdictStatus.NUMERIC_DRIFT.value

    c_ugx = decomposer.decompose("Gateway has UGX 125,000 daily limit [perf_1].")
    v_ugx = verifier.verify_claims(c_ugx, _sample_sources())[0]
    assert v_ugx.violation_code == "TG004"


def test_tg005_uncited_factual_assertion() -> None:
    decomposer = ClaimDecomposer()
    strict_verifier = CitationEntailmentVerifier(strict_citations=True, decomposer=decomposer)
    claims = decomposer.decompose("Gateway P95 latency is 42.8 ms across all nodes.")
    verdicts = strict_verifier.verify_claims(claims, _sample_sources())
    assert verdicts[0].violation_code == "TG005"
    assert verdicts[0].status == VerdictStatus.UNVERIFIED.value


def test_antonym_pairs_immutable_synchronous_and_encrypted() -> None:
    sources = [
        SourceDocument(
            source_id="arch_1",
            title="Architecture Spec",
            content=(
                "Audit logs are immutable after commit. "
                "Replication is asynchronous across regions. "
                "Payloads are encrypted in transit."
            ),
        )
    ]
    decomposer = ClaimDecomposer()
    verifier = CitationEntailmentVerifier(decomposer=decomposer)

    for bad_text in (
        "Audit logs are mutable after commit [arch_1].",
        "Replication is synchronous across regions [arch_1].",
        "Payloads are unencrypted in transit [arch_1].",
    ):
        claims = decomposer.decompose(bad_text)
        verdict = verifier.verify_claims(claims, sources)[0]
        assert verdict.violation_code == "TG003"
        assert verdict.status == VerdictStatus.CONTRADICTED.value


def test_multi_source_synthesis_supported() -> None:
    decomposer = ClaimDecomposer()
    verifier = CitationEntailmentVerifier(decomposer=decomposer)
    claims = decomposer.decompose(
        "Backups are encrypted at rest with AES-256 and P95 latency is 42.8 ms [sec_1, perf_1]."
    )
    verdicts = verifier.verify_claims(claims, _sample_sources())
    assert verdicts[0].passed is True

