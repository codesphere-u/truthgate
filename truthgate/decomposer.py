"""Atomic claim decomposition and linguistic/code feature extraction for TruthGate."""

from __future__ import annotations

import re

from truthgate.models import AtomicClaim, CodeReference, NumericToken

STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "done",
        "for",
        "from",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "may",
        "might",
        "more",
        "most",
        "must",
        "my",
        "of",
        "on",
        "only",
        "or",
        "other",
        "our",
        "ours",
        "out",
        "over",
        "shall",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "upon",
        "us",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
        "also",
        "using",
        "used",
        "uses",
        "via",
        "per",
        "each",
    }
)

NEGATION_MARKERS: frozenset[str] = frozenset(
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
        "prohibited",
        "forbid",
        "forbidden",
        "disabled",
        "disable",
        "unsupported",
        "fails",
        "failed",
        "rejected",
        "reject",
        "denied",
        "deny",
        "disallowed",
        "disallow",
        "invalid",
        "deprecated",
        "blocked",
        "unencrypted",
        "immutable",
        "asynchronous",
        "offline",
        "inactive",
    }
)

# Regex patterns
_MD_LINK_RE = re.compile(
    r"\[(?P<label>[^\]]+)\]\((?:file://)?(?P<path>[A-Za-z0-9_./\-]+\.[A-Za-z0-9_]+)(?:#L(?P<start>\d+)(?:-L?(?P<end>\d+))?)?\)"
)
_BRACKET_FILE_RE = re.compile(
    r"\[(?P<path>[A-Za-z0-9_./\-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|sql|yaml|yml|json|toml|md|sh))"
    r"(?:#L(?P<start>\d+)(?:-L?(?P<end>\d+))?)?\]"
)
_INLINE_PATH_RE = re.compile(
    r"(?:`|\b)(?P<path>(?:[A-Za-z0-9_\-]+/)+[A-Za-z0-9_.\-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|sql|yaml|yml|json|toml|md|sh)"
    r"|[A-Za-z0-9_.\-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|sql|yaml|yml|json|toml|sh))"
    r"(?:#L(?P<start>\d+)(?:-L?(?P<end>\d+))?)?"
    r"(?::(?P<colon_sym>[A-Za-z_][A-Za-z0-9_.]*))?(?:`|\b)"
)
_BACKTICK_SYMBOL_RE = re.compile(
    r"`(?:def\s+|class\s+|fn\s+|func\s+)?(?P<sym>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)(?:\([^`]*\))?`"
)
_CALL_SYMBOL_RE = re.compile(
    r"\b(?:function|method|class|symbol|calls?|invokes?|defines?)\s+`?(?P<sym>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)(?:\(\))?`?"
)

# Citations: [doc_1], [source:doc_1], [doc_1, doc_2], (ref: doc_1), (source: doc_1)
_BRACKET_CITATION_RE = re.compile(
    r"\[(?:source\s*:\s*|ref\s*:\s*)?(?P<ids>[A-Za-z0-9_\-]+(?:\s*,\s*[A-Za-z0-9_\-]+)*)\](?!\()"
)
_PAREN_CITATION_RE = re.compile(
    r"\((?:source|ref|cite)\s*:\s*(?P<ids>[A-Za-z0-9_\-]+(?:\s*,\s*[A-Za-z0-9_\-]+)*)\)",
    re.IGNORECASE,
)

# Versions like v1.2.3 or TLS 1.3 or HTTP/2.0
_VERSION_RE = re.compile(
    r"\b(?:v\d+\.\d+(?:\.\d+)?|(?:TLS|SSL|HTTP|Python|Node|OAuth)\s+\d+\.\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)

# Currency prefix: $1,250.00, UGX 1,250,000, USD 45.5, EUR 100
_CURRENCY_PREFIX_RE = re.compile(
    r"(?P<curr>\$|€|£|\b(?:UGX|USD|EUR|GBP|KES|TZS|NGN|ZAR)\b)\s*(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<dec>\d+))?\b"
)

# Number with optional unit suffix: 42.8 ms, 99.95%, 1,250 req/s, 512 MB, 1000
_NUMBER_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9_./#\-])(?P<num>-?\d{1,3}(?:,\d{3})+|-?\d+)(?:\.(?P<dec>\d+))?\s*(?P<unit>%|(?:ms|s|sec|seconds|min|minutes|hr|hours|days|kb|mb|gb|tb|bps|kbps|mbps|gbps|req/s|rps|qps|hz|khz|mhz|ghz|lines|tokens|retries|workers|bytes|bits|x)\b)?",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z0-9`\[\"\'\-\*])|\n+"
)


class ClaimDecomposer:
    """Splits agent responses into atomic claims and extracts citations, numbers, negations, and code references."""

    def decompose(self, text: str) -> list[AtomicClaim]:
        """Decompose a multi-paragraph markdown response into a list of AtomicClaims."""
        if not text or not text.strip():
            return []

        raw_segments = self._split_into_sentences(text)
        claims: list[AtomicClaim] = []
        for idx, segment in enumerate(raw_segments, start=1):
            cleaned = segment.strip()
            if not cleaned:
                continue
            # Skip pure markdown headers or horizontal rules
            if cleaned.startswith("#") or set(cleaned) <= {"-", "*", "=", " "}:
                continue
            # Strip leading markdown list markers ("- ", "* ", "1. ")
            cleaned = re.sub(r"^(?:[\-\*\+]\s+|\d+\.\s+)", "", cleaned).strip()
            if not cleaned:
                continue

            claim_id = f"claim_{idx}"
            code_refs = self.extract_code_references(cleaned)
            cited_sources = self.extract_citations(cleaned, code_refs)
            numbers = self.extract_numbers(cleaned)
            has_negation = self.detect_negation(cleaned)
            entities = self.extract_entities(cleaned)

            claims.append(
                AtomicClaim(
                    claim_id=claim_id,
                    raw_text=cleaned,
                    cited_sources=cited_sources,
                    numbers=numbers,
                    has_negation=has_negation,
                    entities=entities,
                    code_refs=code_refs,
                )
            )
        return claims

    def _split_into_sentences(self, text: str) -> list[str]:
        """Split text into sentence-level propositions while protecting abbreviations and decimals."""
        protected = text
        # Protect common abbreviations
        for abbr in ("e.g.", "i.e.", "vs.", "etc.", "Mr.", "Dr.", "Prof.", "Fig.", "No."):
            protected = protected.replace(abbr, abbr[:-1] + "<DOT>")

        parts = _SENTENCE_SPLIT_RE.split(protected)
        restored = [p.replace("<DOT>", ".").strip() for p in parts if p.strip()]
        return restored

    def extract_citations(
        self, text: str, code_refs: list[CodeReference] | None = None
    ) -> list[str]:
        """Extract inline source citations such as [doc_1], [source:sec_2], or (ref: doc_3)."""
        code_raws = {c.raw for c in (code_refs or [])}
        code_paths = {c.file_path for c in (code_refs or []) if c.file_path}

        citations: list[str] = []
        for match in _BRACKET_CITATION_RE.finditer(text):
            full = match.group(0)
            if full in code_raws:
                continue
            raw_ids = match.group("ids")
            for item in raw_ids.split(","):
                cid = item.strip()
                if not cid or cid in code_paths:
                    continue
                # Ignore file-like names or line refs
                if "." in cid or cid.startswith("L") and cid[1:].isdigit():
                    continue
                if cid not in citations:
                    citations.append(cid)

        for match in _PAREN_CITATION_RE.finditer(text):
            raw_ids = match.group("ids")
            for item in raw_ids.split(","):
                cid = item.strip()
                if cid and cid not in citations:
                    citations.append(cid)

        return citations

    def extract_numbers(self, text: str) -> list[NumericToken]:
        """Extract numeric literals, percentages, currencies, units, and protocol versions."""
        # Mask out citations, markdown links, file paths, and backtick code identifiers first
        masked = _MD_LINK_RE.sub(" ", text)
        masked = _BRACKET_FILE_RE.sub(" ", masked)
        masked = _INLINE_PATH_RE.sub(" ", masked)
        masked = _BRACKET_CITATION_RE.sub(" ", masked)
        masked = _PAREN_CITATION_RE.sub(" ", masked)
        masked = re.sub(r"`[^`]+`", " ", masked)

        tokens: list[NumericToken] = []
        occupied_spans: list[tuple[int, int]] = []

        def _overlaps(start: int, end: int) -> bool:
            return any(not (end <= s or start >= e) for s, e in occupied_spans)

        # 1. Extract protocol/software versions (e.g. TLS 1.0, TLS 1.3, v2.1.0)
        for m in _VERSION_RE.finditer(masked):
            s, e = m.span()
            occupied_spans.append((s, e))
            raw = m.group(0).strip()
            num_part = re.search(r"\d+\.\d+", raw)
            val = float(num_part.group(0)) if num_part else 0.0
            tokens.append(NumericToken(raw=raw, value=val, unit="version", is_version=True))

        # 2. Extract currency values (e.g. UGX 1,250,000, $1,250.00)
        for m in _CURRENCY_PREFIX_RE.finditer(masked):
            s, e = m.span()
            if _overlaps(s, e):
                continue
            occupied_spans.append((s, e))
            curr = m.group("curr").upper()
            if curr == "$":
                curr = "USD"
            elif curr == "€":
                curr = "EUR"
            elif curr == "£":
                curr = "GBP"
            num_str = m.group("num").replace(",", "")
            dec_str = m.group("dec")
            full_num = f"{num_str}.{dec_str}" if dec_str else num_str
            tokens.append(
                NumericToken(
                    raw=m.group(0).strip(),
                    value=float(full_num),
                    unit=curr,
                    is_version=False,
                )
            )

        # 3. Extract numbers with optional units
        for m in _NUMBER_UNIT_RE.finditer(masked):
            s, e = m.span()
            if _overlaps(s, e):
                continue
            num_str = m.group("num").replace(",", "")
            dec_str = m.group("dec")
            unit = (m.group("unit") or "").strip()
            full_num = f"{num_str}.{dec_str}" if dec_str else num_str
            try:
                val = float(full_num)
            except ValueError:
                continue
            occupied_spans.append((s, e))
            tokens.append(
                NumericToken(
                    raw=m.group(0).strip(),
                    value=val,
                    unit=self._normalize_unit(unit),
                    is_version=False,
                )
            )

        return tokens

    @staticmethod
    def _normalize_unit(unit: str) -> str:
        u = unit.lower().strip()
        aliases = {
            "sec": "s",
            "seconds": "s",
            "min": "minutes",
            "hr": "hours",
            "rps": "req/s",
        }
        return aliases.get(u, u)

    def detect_negation(self, text: str) -> bool:
        """Detect whether a sentence carries negation markers or negative polarity."""
        words = re.findall(r"[A-Za-z']+", text.lower())
        for w in words:
            normalized = w.replace("'", "")
            if normalized in NEGATION_MARKERS or w in NEGATION_MARKERS:
                return True
        return False

    def extract_entities(self, text: str) -> list[str]:
        """Extract normalized content words and technical identifiers from a sentence."""
        # Strip citation tags before extracting content tokens
        cleaned = _BRACKET_CITATION_RE.sub(" ", text)
        cleaned = _PAREN_CITATION_RE.sub(" ", cleaned)
        raw_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.\-]{1,}", cleaned)
        entities: list[str] = []
        for tok in raw_tokens:
            clean_tok = tok.strip("._-").lower()
            if len(clean_tok) < 2 or clean_tok in STOPWORDS or clean_tok in NEGATION_MARKERS:
                continue
            if clean_tok not in entities:
                entities.append(clean_tok)
        return entities

    def extract_code_references(self, text: str) -> list[CodeReference]:
        """Extract file paths, #Lstart-Lend line ranges, and referenced AST symbols."""
        refs: list[CodeReference] = []
        seen_paths: set[tuple[str | None, int | None, int | None, str | None]] = set()
        occupied_spans: list[tuple[int, int]] = []

        def _overlaps(start: int, end: int) -> bool:
            return any(not (end <= s or start >= e) for s, e in occupied_spans)

        # 1. Markdown links: [label](path#L10-L20)
        for m in _MD_LINK_RE.finditer(text):
            occupied_spans.append(m.span())
            label = m.group("label").strip("` ")
            path = m.group("path").strip()
            start = int(m.group("start")) if m.group("start") else None
            end = int(m.group("end")) if m.group("end") else start
            symbol: str | None = None
            # If the label looks like a symbol name (and not a file path)
            if (
                "." not in label
                and "/" not in label
                and re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\(\))?$", label)
            ):
                symbol = label.rstrip("()")
            key = (path, start, end, symbol)
            if key not in seen_paths:
                seen_paths.add(key)
                refs.append(
                    CodeReference(
                        raw=m.group(0),
                        file_path=path,
                        start_line=start,
                        end_line=end,
                        symbol=symbol,
                    )
                )

        # 2. Bracketed file links: [src/auth/jwt.py#L120-L150]
        for m in _BRACKET_FILE_RE.finditer(text):
            if _overlaps(*m.span()):
                continue
            occupied_spans.append(m.span())
            path = m.group("path").strip()
            start = int(m.group("start")) if m.group("start") else None
            end = int(m.group("end")) if m.group("end") else start
            key = (path, start, end, None)
            if key not in seen_paths:
                seen_paths.add(key)
                refs.append(
                    CodeReference(
                        raw=m.group(0),
                        file_path=path,
                        start_line=start,
                        end_line=end,
                        symbol=None,
                    )
                )

        # 3. Inline file paths: `src/auth/jwt.py#L10-L20` or `src/auth/jwt.py:verify_token`
        for m in _INLINE_PATH_RE.finditer(text):
            if _overlaps(*m.span()):
                continue
            occupied_spans.append(m.span())
            path = m.group("path").strip()
            start = int(m.group("start")) if m.group("start") else None
            end = int(m.group("end")) if m.group("end") else start
            colon_sym = m.group("colon_sym")
            key = (path, start, end, colon_sym)
            if key not in seen_paths:
                seen_paths.add(key)
                refs.append(
                    CodeReference(
                        raw=m.group(0).strip("`"),
                        file_path=path,
                        start_line=start,
                        end_line=end,
                        symbol=colon_sym,
                    )
                )

        # 4. Extract standalone symbol references in backticks or explicit calls
        # If the sentence already mentions a single file_path, bind unattached symbols to that file_path!
        primary_file = refs[0].file_path if len(refs) == 1 else None

        found_symbols: list[tuple[str, str]] = []
        for m in _BACKTICK_SYMBOL_RE.finditer(text):
            if _overlaps(*m.span()):
                continue
            raw_tok = m.group(0)
            sym = m.group("sym")
            # Must look like a function call `foo()`, qualified `Class.method`, `def foo`, `class Bar`,
            # or snake_case / CamelCase identifier when accompanied by a file or code keyword
            is_explicit_code = (
                "(" in raw_tok
                or "." in sym
                or raw_tok.startswith(("`def ", "`class ", "`fn ", "`func "))
                or "_" in sym
                or (sym[0].isupper() and any(c.islower() for c in sym))
            )
            if is_explicit_code and sym.lower() not in STOPWORDS:
                found_symbols.append((raw_tok.strip("`"), sym))

        for m in _CALL_SYMBOL_RE.finditer(text):
            sym = m.group("sym")
            if sym and sym.lower() not in STOPWORDS:
                found_symbols.append((m.group(0), sym))

        for raw_sym, sym in found_symbols:
            # Check if we already recorded this symbol
            if any(r.symbol == sym for r in refs):
                continue
            # If primary_file has a ref with symbol=None and no line range issues, attach or add ref
            key = (primary_file, None, None, sym)
            if key not in seen_paths:
                seen_paths.add(key)
                refs.append(
                    CodeReference(
                        raw=raw_sym,
                        file_path=primary_file,
                        start_line=None,
                        end_line=None,
                        symbol=sym,
                    )
                )

        return refs
