"""Unit tests for truthgate.decomposer (ClaimDecomposer)."""

from __future__ import annotations

from truthgate.decomposer import ClaimDecomposer


def test_decompose_multi_paragraph_and_markdown_lists() -> None:
    decomposer = ClaimDecomposer()
    text = """
    # Audit Summary
    - Gateway routing latency is 42.8 ms in Kampala e.g. edge nodes [sla_metrics].
    - Settlement volume is capped at UGX 1,250,000 [fin_ledger].

    TLS 1.0 is not permitted in production [sec_policy].
    """
    claims = decomposer.decompose(text)
    assert len(claims) == 3
    assert claims[0].cited_sources == ["sla_metrics"]
    assert claims[1].cited_sources == ["fin_ledger"]
    assert claims[2].cited_sources == ["sec_policy"]
    assert claims[2].has_negation is True


def test_extract_citations_multiple_formats() -> None:
    decomposer = ClaimDecomposer()
    c1 = decomposer.extract_citations("Supported by [doc_1] and [source:doc_2, doc_3] plus (ref: doc_4).")
    assert c1 == ["doc_1", "doc_2", "doc_3", "doc_4"]


def test_extract_numbers_currencies_units_and_versions() -> None:
    decomposer = ClaimDecomposer()
    text = (
        "Latency is 42.8 ms with 99.95% uptime, reserve fee $1,250.00, "
        "and cap UGX 50,000 under TLS 1.3 [doc_1]."
    )
    nums = decomposer.extract_numbers(text)
    raws = [n.raw for n in nums]
    assert "TLS 1.3" in raws
    assert "$1,250.00" in raws
    assert "UGX 50,000" in raws
    assert "42.8 ms" in raws
    assert "99.95%" in raws
    # Ensure citation number '1' in [doc_1] was NOT extracted as a numeric token
    assert not any(n.value == 1.0 and not n.is_version for n in nums)


def test_detect_negation_markers() -> None:
    decomposer = ClaimDecomposer()
    assert decomposer.detect_negation("TLS 1.0 is not permitted in production.") is True
    assert decomposer.detect_negation("Feature is disabled by default.") is True
    assert decomposer.detect_negation("Zero retries are allowed without mTLS.") is True
    assert decomposer.detect_negation("All endpoints enforce TLS 1.3.") is False


def test_extract_code_references_markdown_and_backticks() -> None:
    decomposer = ClaimDecomposer()
    text = (
        "Check [jwt.py](src/auth/jwt.py#L10-L20) and `TokenValidator.verify_token()` "
        "or `[src/ledger/settlement.py#L5-L12]`."
    )
    refs = decomposer.extract_code_references(text)
    paths = [r.file_path for r in refs if r.file_path]
    symbols = [r.symbol for r in refs if r.symbol]
    assert "src/auth/jwt.py" in paths
    assert "src/ledger/settlement.py" in paths
    assert "TokenValidator.verify_token" in symbols
