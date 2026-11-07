"""TruthGate: Deterministic Agent Hallucination Auditor, Citation-Span Entailment Verifier & Workspace Symbol Grounding Engine."""

from truthgate.benchmark import get_benchmark_scenarios, run_benchmark_suite
from truthgate.corrector import SurgicalRepairCompiler, TruthGateVerifier
from truthgate.decomposer import ClaimDecomposer
from truthgate.entailment import CitationEntailmentVerifier
from truthgate.grounding import WorkspaceSymbolGrounder
from truthgate.mcp_server import TruthGateMCPServer
from truthgate.models import (
    RULE_MAP,
    RULES,
    AtomicClaim,
    ClaimVerdict,
    CodeReference,
    GroundingReport,
    NumericToken,
    RuleDefinition,
    SourceDocument,
    VerdictStatus,
    VerificationResult,
)

__version__ = "1.0.0"

__all__ = [
    "__version__",
    "AtomicClaim",
    "CitationEntailmentVerifier",
    "ClaimDecomposer",
    "ClaimVerdict",
    "CodeReference",
    "GroundingReport",
    "NumericToken",
    "RULE_MAP",
    "RULES",
    "RuleDefinition",
    "SourceDocument",
    "SurgicalRepairCompiler",
    "TruthGateMCPServer",
    "TruthGateVerifier",
    "VerdictStatus",
    "VerificationResult",
    "WorkspaceSymbolGrounder",
    "get_benchmark_scenarios",
    "run_benchmark_suite",
]
