"""Core domain models and deterministic verification rule definitions for TruthGate."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class VerdictStatus(str, Enum):
    """Deterministic verification status for an atomic claim or code reference."""

    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNVERIFIED = "UNVERIFIED"
    PHANTOM_CITATION = "PHANTOM_CITATION"
    NUMERIC_DRIFT = "NUMERIC_DRIFT"
    BROKEN_CODE_REF = "BROKEN_CODE_REF"


@dataclass(frozen=True)
class RuleDefinition:
    """Metadata describing one of TruthGate's 8 deterministic verification rules."""

    code: str
    name: str
    category: str
    default_status: str
    severity: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


RULES: tuple[RuleDefinition, ...] = (
    RuleDefinition(
        code="TG001",
        name="Phantom Source Citation",
        category="entailment",
        default_status=VerdictStatus.PHANTOM_CITATION.value,
        severity="CRITICAL",
        description="Claim cites a [source_id] that does not exist in the provided context corpus.",
    ),
    RuleDefinition(
        code="TG002",
        name="Low Lexical/Entity Support",
        category="entailment",
        default_status=VerdictStatus.UNVERIFIED.value,
        severity="HIGH",
        description="IDF-weighted content-token and technical-entity overlap between claim and cited source falls below threshold.",
    ),
    RuleDefinition(
        code="TG003",
        name="Negation Polarity Inversion",
        category="entailment",
        default_status=VerdictStatus.CONTRADICTED.value,
        severity="CRITICAL",
        description="High entity/predicate overlap with a source sentence, but opposite negation or antonym polarity.",
    ),
    RuleDefinition(
        code="TG004",
        name="Numeric & Magnitude Drift",
        category="entailment",
        default_status=VerdictStatus.NUMERIC_DRIFT.value,
        severity="CRITICAL",
        description="Claim introduces a numeric/percentage/currency quantity that does not match any value in the cited source.",
    ),
    RuleDefinition(
        code="TG005",
        name="Uncited Factual Assertion",
        category="entailment",
        default_status=VerdictStatus.UNVERIFIED.value,
        severity="MEDIUM",
        description="Factual or numeric proposition in strict-grounding mode lacks any supporting source citation or workspace evidence.",
    ),
    RuleDefinition(
        code="TG006",
        name="Non-Existent File Path",
        category="workspace_grounding",
        default_status=VerdictStatus.BROKEN_CODE_REF.value,
        severity="CRITICAL",
        description="Referenced file path does not exist inside the configured workspace root.",
    ),
    RuleDefinition(
        code="TG007",
        name="Out-of-Bounds Line Range",
        category="workspace_grounding",
        default_status=VerdictStatus.BROKEN_CODE_REF.value,
        severity="HIGH",
        description="Referenced line range (#Lstart-Lend) exceeds the actual line count of the workspace file.",
    ),
    RuleDefinition(
        code="TG008",
        name="Phantom Code Symbol",
        category="workspace_grounding",
        default_status=VerdictStatus.BROKEN_CODE_REF.value,
        severity="CRITICAL",
        description="Referenced function, class, or method symbol does not exist in the target file's AST or symbol table.",
    ),
)

RULE_MAP: dict[str, RuleDefinition] = {rule.code: rule for rule in RULES}


@dataclass(slots=True)
class SourceDocument:
    """A grounding context document retrieved via RAG, MCP tool output, or system context."""

    source_id: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_id or not self.source_id.strip():
            raise ValueError("SourceDocument.source_id must be a non-empty string.")
        if self.content is None:
            raise ValueError("SourceDocument.content cannot be None.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "content": self.content,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class NumericToken:
    """Normalized numeric literal with optional unit or currency extracted from text."""

    raw: str
    value: float
    unit: str = ""
    is_version: bool = False

    def matches(self, other: NumericToken, rel_tol: float = 1e-4) -> bool:
        """Check whether two numeric tokens represent the same value and compatible unit."""
        if self.is_version != other.is_version:
            return False
        if self.is_version and other.is_version:
            return self.raw.lstrip("vV") == other.raw.lstrip("vV")
        if self.unit and other.unit and self.unit.lower() != other.unit.lower():
            return False
        if self.value == 0.0 and other.value == 0.0:
            return True
        denom = max(abs(self.value), abs(other.value), 1e-12)
        return abs(self.value - other.value) / denom <= rel_tol

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "value": self.value,
            "unit": self.unit,
            "is_version": self.is_version,
        }


@dataclass(slots=True)
class CodeReference:
    """A file path, line range, or AST code symbol referenced in an agent response."""

    raw: str
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    symbol: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "symbol": self.symbol,
        }


@dataclass(slots=True)
class AtomicClaim:
    """A single atomic sentence/proposition extracted from an agent response."""

    claim_id: str
    raw_text: str
    cited_sources: list[str] = field(default_factory=list)
    numbers: list[NumericToken] = field(default_factory=list)
    has_negation: bool = False
    entities: list[str] = field(default_factory=list)
    code_refs: list[CodeReference] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.claim_id:
            raise ValueError("AtomicClaim.claim_id must be non-empty.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "raw_text": self.raw_text,
            "cited_sources": list(self.cited_sources),
            "numbers": [n.to_dict() for n in self.numbers],
            "has_negation": self.has_negation,
            "entities": list(self.entities),
            "code_refs": [c.to_dict() for c in self.code_refs],
        }


@dataclass(slots=True)
class ClaimVerdict:
    """Verification outcome for a single AtomicClaim or CodeReference."""

    claim_id: str
    claim_text: str
    status: str
    confidence: float
    explanation: str
    matched_span: str = ""
    violation_code: str = ""
    source_id: str = ""

    def __post_init__(self) -> None:
        valid_statuses = {s.value for s in VerdictStatus}
        if self.status not in valid_statuses:
            raise ValueError(
                f"Invalid ClaimVerdict.status '{self.status}'. Must be one of {sorted(valid_statuses)}."
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("ClaimVerdict.confidence must be between 0.0 and 1.0.")

    @property
    def passed(self) -> bool:
        return self.status == VerdictStatus.SUPPORTED.value and not self.violation_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_text": self.claim_text,
            "status": self.status,
            "confidence": round(self.confidence, 4),
            "explanation": self.explanation,
            "matched_span": self.matched_span,
            "violation_code": self.violation_code,
            "source_id": self.source_id,
            "passed": self.passed,
        }


@dataclass(slots=True)
class GroundingReport:
    """Result of verifying code/workspace file paths, line ranges, and AST symbols."""

    workspace_root: str
    total_references: int
    valid_references: int
    broken_references: int
    verdicts: list[ClaimVerdict] = field(default_factory=list)
    available_symbols: dict[str, list[str]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.broken_references == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_root": self.workspace_root,
            "passed": self.passed,
            "total_references": self.total_references,
            "valid_references": self.valid_references,
            "broken_references": self.broken_references,
            "verdicts": [v.to_dict() for v in self.verdicts],
            "available_symbols": dict(self.available_symbols),
        }


@dataclass(slots=True)
class VerificationResult:
    """Complete deterministic audit report for an agent response."""

    passed: bool
    overall_score: float
    total_claims: int
    supported_claims: int
    failed_claims: int
    violation_counts: dict[str, int]
    claim_verdicts: list[ClaimVerdict]
    grounding_report: GroundingReport | None = None
    repair_prompt: str = ""
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "overall_score": round(self.overall_score, 4),
            "total_claims": self.total_claims,
            "supported_claims": self.supported_claims,
            "failed_claims": self.failed_claims,
            "violation_counts": dict(self.violation_counts),
            "claim_verdicts": [v.to_dict() for v in self.claim_verdicts],
            "grounding_report": self.grounding_report.to_dict() if self.grounding_report else None,
            "repair_prompt": self.repair_prompt,
            "elapsed_ms": round(self.elapsed_ms, 4),
        }
