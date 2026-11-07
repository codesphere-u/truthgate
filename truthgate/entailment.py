"""Deterministic Citation-Span Entailment Verifier implementing rules TG001-TG005."""

from __future__ import annotations

import math
import re
from truthgate.decomposer import ClaimDecomposer
from truthgate.models import AtomicClaim, ClaimVerdict, NumericToken, SourceDocument, VerdictStatus

EXPLICIT_NEGATIONS: frozenset[str] = frozenset(
    {
        "not",
        "never",
        "no",
        "none",
        "neither",
        "nor",
        "cannot",
        "cant",
        "wont",
        "doesnt",
        "isnt",
        "arent",
        "wasnt",
        "werent",
        "shouldnt",
        "mustnt",
        "without",
        "zero",
    }
)

# Bidirectional antonym pairs: (positive/affirmative pole, negative/opposing pole)
ANTONYM_PAIRS: tuple[tuple[str, str], ...] = (
    ("enabled", "disabled"),
    ("enable", "disable"),
    ("allowed", "prohibited"),
    ("allowed", "forbidden"),
    ("allowed", "disallowed"),
    ("allow", "prohibit"),
    ("allow", "forbid"),
    ("allow", "disallow"),
    ("permitted", "prohibited"),
    ("permitted", "forbidden"),
    ("permitted", "disallowed"),
    ("permit", "prohibit"),
    ("permit", "forbid"),
    ("supported", "unsupported"),
    ("support", "unsupport"),
    ("encrypted", "unencrypted"),
    ("encrypted", "plaintext"),
    ("synchronous", "asynchronous"),
    ("sync", "async"),
    ("mutable", "immutable"),
    ("required", "optional"),
    ("mandatory", "optional"),
    ("public", "private"),
    ("active", "inactive"),
    ("online", "offline"),
    ("stateful", "stateless"),
    ("valid", "invalid"),
    ("accepted", "rejected"),
    ("accept", "reject"),
    ("granted", "denied"),
    ("grant", "deny"),
    ("succeeds", "fails"),
    ("succeeded", "failed"),
    ("pass", "fail"),
    ("passes", "fails"),
    ("safe", "unsafe"),
    ("secure", "insecure"),
    ("blocking", "nonblocking"),
    ("blocked", "unblocked"),
)


def _stem_token(token: str) -> str:
    """Lightweight deterministic stemmer for technical English tokens."""
    t = token.lower().strip("._-")
    if len(t) <= 4:
        return t
    for suffix in ("ation", "ition", "ments", "ment", "ingly", "edly", "ing", "ed", "ies", "es", "s"):
        if t.endswith(suffix) and len(t) - len(suffix) >= 3:
            if suffix == "ies":
                return t[:-3] + "y"
            return t[: -len(suffix)]
    return t


class CitationEntailmentVerifier:
    """Evaluates AtomicClaims against SourceDocuments across deterministic rules TG001-TG005."""

    def __init__(
        self,
        min_lexical_support: float = 0.30,
        strict_citations: bool = True,
        decomposer: ClaimDecomposer | None = None,
    ) -> None:
        self.min_lexical_support = min_lexical_support
        self.strict_citations = strict_citations
        self.decomposer = decomposer or ClaimDecomposer()
        self._idf_cache: dict[tuple[tuple[str, int], ...], dict[str, float]] = {}

    def verify_claims(
        self,
        claims: list[AtomicClaim],
        sources: list[SourceDocument],
        has_workspace: bool = False,
    ) -> list[ClaimVerdict]:
        """Verify a sequence of atomic claims against the provided source corpus."""
        source_map: dict[str, SourceDocument] = {s.source_id: s for s in sources}
        cache_key = tuple((s.source_id, len(s.content)) for s in sources)
        if cache_key not in self._idf_cache:
            self._idf_cache[cache_key] = self._compute_idf(sources)
        idf_map = self._idf_cache[cache_key]
        verdicts: list[ClaimVerdict] = []

        for claim in claims:
            verdict = self.verify_single_claim(
                claim=claim,
                source_map=source_map,
                idf_map=idf_map,
                has_workspace=has_workspace,
            )
            verdicts.append(verdict)
        return verdicts

    def verify_single_claim(
        self,
        claim: AtomicClaim,
        source_map: dict[str, SourceDocument],
        idf_map: dict[str, float] | None = None,
        has_workspace: bool = False,
    ) -> ClaimVerdict:
        """Evaluate a single AtomicClaim against TG001-TG005."""
        idf = idf_map or {}

        # Rule TG005: Uncited Factual Assertion
        if not claim.cited_sources:
            # If the claim is purely a workspace code reference and workspace grounding is enabled,
            # allow WorkspaceSymbolGrounder to verify it rather than failing TG005.
            if has_workspace and claim.code_refs:
                return ClaimVerdict(
                    claim_id=claim.claim_id,
                    claim_text=claim.raw_text,
                    status=VerdictStatus.SUPPORTED.value,
                    confidence=0.95,
                    explanation="Claim contains workspace code references delegated to WorkspaceSymbolGrounder.",
                    matched_span="",
                    violation_code="",
                    source_id="workspace",
                )
            if self.strict_citations:
                return ClaimVerdict(
                    claim_id=claim.claim_id,
                    claim_text=claim.raw_text,
                    status=VerdictStatus.UNVERIFIED.value,
                    confidence=0.0,
                    explanation=(
                        "TG005 Uncited Factual Assertion: Claim makes a factual proposition without "
                        "citing any source document ID (e.g. [doc_1])."
                    ),
                    matched_span="",
                    violation_code="TG005",
                    source_id="",
                )

        # Rule TG001: Phantom Source Citation
        missing_sources = [sid for sid in claim.cited_sources if sid not in source_map]
        if missing_sources:
            available = ", ".join(sorted(source_map.keys())) if source_map else "none"
            return ClaimVerdict(
                claim_id=claim.claim_id,
                claim_text=claim.raw_text,
                status=VerdictStatus.PHANTOM_CITATION.value,
                confidence=0.0,
                explanation=(
                    f"TG001 Phantom Source Citation: Cited source ID(s) {missing_sources} do not exist "
                    f"in the provided context corpus (available: {available})."
                ),
                matched_span="",
                violation_code="TG001",
                source_id=missing_sources[0],
            )

        # Gather candidate sentences from all cited sources
        cited_docs = [source_map[sid] for sid in claim.cited_sources]
        best_span = ""
        best_source_id = cited_docs[0].source_id if cited_docs else ""
        best_score = -1.0

        all_doc_sentences: list[tuple[str, str]] = []
        for doc in cited_docs:
            sents = self.decomposer._split_into_sentences(doc.content)
            if not sents and doc.content.strip():
                sents = [doc.content.strip()]
            for s in sents:
                all_doc_sentences.append((doc.source_id, s))
                score = self._lexical_support_score(claim.entities, s, idf)
                if score > best_score:
                    best_score = score
                    best_span = s
                    best_source_id = doc.source_id

        # Also compute whole-document entity coverage in case a claim synthesizes two adjacent sentences
        combined_doc_text = " ".join(doc.content for doc in cited_docs)
        doc_level_score = self._lexical_support_score(claim.entities, combined_doc_text, idf)
        effective_support = max(best_score, doc_level_score * 0.9)

        # Rule TG002: Low Lexical/Entity Support
        if effective_support < self.min_lexical_support:
            return ClaimVerdict(
                claim_id=claim.claim_id,
                claim_text=claim.raw_text,
                status=VerdictStatus.UNVERIFIED.value,
                confidence=round(max(0.0, effective_support), 4),
                explanation=(
                    f"TG002 Low Lexical/Entity Support: Claim has insufficient entity/lexical overlap "
                    f"({effective_support:.2f} < {self.min_lexical_support:.2f}) with cited source [{best_source_id}]."
                ),
                matched_span=best_span,
                violation_code="TG002",
                source_id=best_source_id,
            )

        # Rule TG003: Negation & Antonym Polarity Inversion
        # Check against the best matching sentence (and any high-overlap sentence in the cited docs)
        polarity_conflict = self._check_polarity_inversion(claim.raw_text, best_span)
        if not polarity_conflict:
            for sid, sent in all_doc_sentences:
                if self._lexical_support_score(claim.entities, sent, idf) >= 0.45:
                    conflict = self._check_polarity_inversion(claim.raw_text, sent)
                    if conflict:
                        polarity_conflict = conflict
                        best_span = sent
                        best_source_id = sid
                        break

        if polarity_conflict:
            return ClaimVerdict(
                claim_id=claim.claim_id,
                claim_text=claim.raw_text,
                status=VerdictStatus.CONTRADICTED.value,
                confidence=round(effective_support, 4),
                explanation=(
                    f"TG003 Negation Polarity Inversion: {polarity_conflict} "
                    f"(Source [{best_source_id}] states: \"{best_span}\")."
                ),
                matched_span=best_span,
                violation_code="TG003",
                source_id=best_source_id,
            )

        # Rule TG004: Numeric & Magnitude Drift
        if claim.numbers:
            source_numbers = self.decomposer.extract_numbers(combined_doc_text)
            span_numbers = self.decomposer.extract_numbers(best_span)
            drift_details = self._check_numeric_drift(claim.numbers, span_numbers, source_numbers)
            if drift_details:
                return ClaimVerdict(
                    claim_id=claim.claim_id,
                    claim_text=claim.raw_text,
                    status=VerdictStatus.NUMERIC_DRIFT.value,
                    confidence=round(effective_support, 4),
                    explanation=(
                        f"TG004 Numeric & Magnitude Drift: {drift_details} "
                        f"(Source [{best_source_id}] states: \"{best_span}\")."
                    ),
                    matched_span=best_span,
                    violation_code="TG004",
                    source_id=best_source_id,
                )

        return ClaimVerdict(
            claim_id=claim.claim_id,
            claim_text=claim.raw_text,
            status=VerdictStatus.SUPPORTED.value,
            confidence=round(min(1.0, max(0.75, effective_support)), 4),
            explanation=f"Claim is entailed by source [{best_source_id}] with support score {effective_support:.2f}.",
            matched_span=best_span,
            violation_code="",
            source_id=best_source_id,
        )

    def _compute_idf(self, sources: list[SourceDocument]) -> dict[str, float]:
        """Compute smoothed inverse document frequency across the source corpus."""
        n_docs = max(1, len(sources))
        df: dict[str, int] = {}
        for doc in sources:
            tokens = {_stem_token(t) for t in self.decomposer.extract_entities(doc.content)}
            for tok in tokens:
                df[tok] = df.get(tok, 0) + 1
        return {
            tok: math.log((1.0 + n_docs) / (1.0 + count)) + 1.0 for tok, count in df.items()
        }

    def _lexical_support_score(
        self, claim_entities: list[str], source_text: str, idf: dict[str, float]
    ) -> float:
        """Compute IDF-weighted entity & stem overlap between a claim and a source text span."""
        if not claim_entities:
            return 1.0

        source_raw_tokens = {
            t.lower().strip("._-")
            for t in re.findall(r"[A-Za-z0-9_.\-]+", source_text)
            if len(t) >= 2
        }
        source_stems = {_stem_token(t) for t in source_raw_tokens}

        # Filter out antonym/polarity words from claim_entities when measuring topic/subject overlap
        polarity_vocab = {w for pair in ANTONYM_PAIRS for w in pair}
        subject_entities = [e for e in claim_entities if e not in polarity_vocab]
        eval_entities = subject_entities if subject_entities else claim_entities

        matched_weight = 0.0
        total_weight = 0.0
        for ent in eval_entities:
            stem = _stem_token(ent)
            weight = idf.get(stem, 1.2)
            # Boost technical identifiers (containing digits, underscores, or acronyms)
            if any(c.isdigit() or c == "_" for c in ent):
                weight *= 1.35
            total_weight += weight
            if ent in source_raw_tokens or stem in source_stems:
                matched_weight += weight
            else:
                # Check sub-token match for hyphenated or dotted identifiers
                subparts = [p for p in re.split(r"[._\-]", ent) if len(p) >= 3]
                if subparts and all(_stem_token(p) in source_stems for p in subparts):
                    matched_weight += weight * 0.85

        if total_weight == 0.0:
            return 1.0
        return min(1.0, matched_weight / total_weight)

    def _check_polarity_inversion(self, claim_text: str, source_span: str) -> str:
        """Detect explicit negation flips or antonym inversions between claim and source span."""
        if not source_span:
            return ""

        claim_words = [
            w.replace("'", "") for w in re.findall(r"[A-Za-z']+", claim_text.lower())
        ]
        source_words = [
            w.replace("'", "") for w in re.findall(r"[A-Za-z']+", source_span.lower())
        ]
        claim_word_set = set(claim_words)
        source_word_set = set(source_words)
        claim_stems = {_stem_token(w) for w in claim_words}
        source_stems = {_stem_token(w) for w in source_words}

        claim_explicit_neg = bool(claim_word_set & EXPLICIT_NEGATIONS)
        source_explicit_neg = bool(source_word_set & EXPLICIT_NEGATIONS)

        # Check antonym pairs first
        antonym_flipped = False
        antonym_desc = ""
        for pos_word, neg_word in ANTONYM_PAIRS:
            pos_stem = _stem_token(pos_word)
            neg_stem = _stem_token(neg_word)
            claim_has_pos = pos_word in claim_word_set or pos_stem in claim_stems
            claim_has_neg = neg_word in claim_word_set or neg_stem in claim_stems
            src_has_pos = pos_word in source_word_set or pos_stem in source_stems
            src_has_neg = neg_word in source_word_set or neg_stem in source_stems

            if (claim_has_pos and src_has_neg and not claim_has_neg) or (
                claim_has_neg and src_has_pos and not claim_has_pos
            ):
                antonym_flipped = True
                c_term = pos_word if claim_has_pos else neg_word
                s_term = neg_word if src_has_neg else pos_word
                antonym_desc = f"Claim uses '{c_term}' where source uses opposing term '{s_term}'"
                break

        # Algebraic XOR of explicit negation flip and antonym flip:
        # - If antonym flipped and explicit negation did NOT flip -> contradiction!
        # - If explicit negation flipped and antonym did NOT flip -> contradiction!
        # - If BOTH flipped (e.g. "not disabled" vs "enabled") -> double negative cancels out!
        if antonym_flipped and (claim_explicit_neg == source_explicit_neg):
            return antonym_desc

        if (claim_explicit_neg != source_explicit_neg) and not antonym_flipped:
            if source_explicit_neg and not claim_explicit_neg:
                neg_tokens = sorted(source_word_set & EXPLICIT_NEGATIONS)
                return f"Claim omits negation marker {neg_tokens} present in cited source"
            else:
                neg_tokens = sorted(claim_word_set & EXPLICIT_NEGATIONS)
                return f"Claim introduces negation marker {neg_tokens} contradicting affirmative source"

        return ""

    def _check_numeric_drift(
        self,
        claim_numbers: list[NumericToken],
        span_numbers: list[NumericToken],
        doc_numbers: list[NumericToken],
    ) -> str:
        """Verify that every numeric quantity in the claim matches the cited source."""
        candidates = span_numbers if span_numbers else doc_numbers
        all_candidates = list(span_numbers) + [
            n for n in doc_numbers if n not in span_numbers
        ]

        for c_num in claim_numbers:
            if any(c_num.matches(s_num) for s_num in all_candidates):
                continue
            # Found a numeric mismatch! Format a clear diagnostic explanation.
            expected_Pool = candidates if candidates else all_candidates
            if expected_Pool:
                expected_str = ", ".join(n.raw for n in expected_Pool)
                return (
                    f"Claim reports '{c_num.raw}' (value={c_num.value:g}), which does not match "
                    f"source value(s) [{expected_str}]"
                )
            return (
                f"Claim reports numeric quantity '{c_num.raw}' (value={c_num.value:g}), "
                f"but cited source contains no matching numeric literal"
            )
        return ""
