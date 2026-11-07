"""Surgical Self-Correction Prompt Compiler and End-to-End Verification Orchestrator."""

from __future__ import annotations

import time
from pathlib import Path
from truthgate.decomposer import ClaimDecomposer
from truthgate.entailment import CitationEntailmentVerifier
from truthgate.grounding import WorkspaceSymbolGrounder
from truthgate.models import (
    RULE_MAP,
    ClaimVerdict,
    GroundingReport,
    SourceDocument,
    VerdictStatus,
    VerificationResult,
)

_REPAIR_ACTION_BY_RULE: dict[str, str] = {
    "TG001": "Replace the phantom citation ID with a valid source_id from the context corpus or remove the unsupported claim.",
    "TG002": "Rewrite the claim to strictly reflect entities and predicates attested in the cited source document.",
    "TG003": "Invert the negation/antonym polarity so the claim matches the affirmative/negative stance of the source span.",
    "TG004": "Replace the drifted numeric value/unit/currency with the exact figure from the ground-truth source span.",
    "TG005": "Attach a valid [source_id] citation backing this assertion or remove the uncited factual claim.",
    "TG006": "Update the file path to an existing file in the workspace or remove the non-existent file reference.",
    "TG007": "Adjust the #Lstart-Lend line range so it falls within the actual line count of the file.",
    "TG008": "Replace the hallucinated function/class/method name with an actual symbol present in the file's AST.",
}


class SurgicalRepairCompiler:
    """Compiles compact, deterministic self-correction prompts from failed ClaimVerdicts."""

    def compile(
        self,
        claim_verdicts: list[ClaimVerdict],
        grounding_report: GroundingReport | None = None,
        sources: list[SourceDocument] | None = None,
    ) -> str:
        """Compile a surgical repair prompt if any claim or code reference failed verification."""
        failed: list[ClaimVerdict] = []
        seen_failures: set[tuple[str, str]] = set()
        for v in claim_verdicts:
            if not v.passed:
                key = (v.violation_code, v.explanation)
                if key not in seen_failures:
                    seen_failures.add(key)
                    failed.append(v)
        if grounding_report:
            for gv in grounding_report.verdicts:
                if not gv.passed:
                    key = (gv.violation_code, gv.explanation)
                    if key not in seen_failures:
                        seen_failures.add(key)
                        failed.append(gv)

        if not failed:
            return ""

        source_map = {s.source_id: s for s in (sources or [])}
        codes = sorted({v.violation_code for v in failed if v.violation_code})
        lines: list[str] = [
            f"[TRUTHGATE VERIFICATION FAILED — {len(failed)} violation(s) detected: {', '.join(codes)}]",
            "Rewrite your response to fix ONLY the following deterministic verification failures:",
            "",
        ]

        for idx, verdict in enumerate(failed, start=1):
            rule = RULE_MAP.get(verdict.violation_code)
            rule_title = f"{verdict.violation_code} ({rule.name})" if rule else verdict.status
            lines.append(f"{idx}. [{rule_title}] Offending statement: \"{verdict.claim_text}\"")
            lines.append(f"   - Diagnostic: {verdict.explanation}")

            evidence = verdict.matched_span
            if not evidence and verdict.source_id and verdict.source_id in source_map:
                evidence = source_map[verdict.source_id].content.strip()
            if evidence:
                lines.append(f"   - Ground-Truth Evidence: \"{evidence}\"")

            action = _REPAIR_ACTION_BY_RULE.get(
                verdict.violation_code,
                "Correct or remove the unverified statement using the ground-truth context.",
            )
            lines.append(f"   - Required Fix: {action}")
            lines.append("")

        lines.append(
            "Preserve all verified claims unchanged and do not introduce new uncited facts or unverified code symbols."
        )
        return "\n".join(lines)


class TruthGateVerifier:
    """High-speed deterministic verification gate combining entailment, AST grounding, and repair compilation."""

    def __init__(
        self,
        min_lexical_support: float = 0.30,
        strict_citations: bool = True,
    ) -> None:
        self.decomposer = ClaimDecomposer()
        self.entailment = CitationEntailmentVerifier(
            min_lexical_support=min_lexical_support,
            strict_citations=strict_citations,
            decomposer=self.decomposer,
        )
        self.grounder = WorkspaceSymbolGrounder(decomposer=self.decomposer)
        self.compiler = SurgicalRepairCompiler()

    def verify(
        self,
        response_text: str,
        sources: list[SourceDocument] | None = None,
        workspace_root: str | Path | None = None,
    ) -> VerificationResult:
        """Run full deterministic audit (TG001-TG008) on an agent response."""
        t0 = time.perf_counter()
        source_list = list(sources or [])
        has_workspace = workspace_root is not None

        claims = self.decomposer.decompose(response_text)
        claim_verdicts: list[ClaimVerdict] = []

        # Run citation entailment verification if sources are provided or strict_citations is active
        if source_list or not has_workspace:
            claim_verdicts = self.entailment.verify_claims(
                claims=claims,
                sources=source_list,
                has_workspace=has_workspace,
            )
        else:
            # Workspace-only verification mode
            for c in claims:
                claim_verdicts.append(
                    ClaimVerdict(
                        claim_id=c.claim_id,
                        claim_text=c.raw_text,
                        status=VerdictStatus.SUPPORTED.value,
                        confidence=1.0,
                        explanation="Workspace grounding mode active.",
                        matched_span="",
                        violation_code="",
                        source_id="workspace",
                    )
                )

        grounding_report: GroundingReport | None = None
        if workspace_root is not None:
            grounding_report = self.grounder.ground_claims(claims, workspace_root)
            # Merge broken code reference verdicts into claim_verdicts if a claim passed entailment
            # but had a broken code reference
            broken_by_claim: dict[str, list[ClaimVerdict]] = {}
            for gv in grounding_report.verdicts:
                base_claim_id = gv.claim_id.split("_ref_")[0]
                if not gv.passed:
                    broken_by_claim.setdefault(base_claim_id, []).append(gv)

            updated_verdicts: list[ClaimVerdict] = []
            for cv in claim_verdicts:
                if cv.passed and cv.claim_id in broken_by_claim:
                    first_broken = broken_by_claim[cv.claim_id][0]
                    updated_verdicts.append(
                        ClaimVerdict(
                            claim_id=cv.claim_id,
                            claim_text=cv.claim_text,
                            status=first_broken.status,
                            confidence=first_broken.confidence,
                            explanation=first_broken.explanation,
                            matched_span=first_broken.matched_span,
                            violation_code=first_broken.violation_code,
                            source_id=first_broken.source_id,
                        )
                    )
                else:
                    updated_verdicts.append(cv)
            claim_verdicts = updated_verdicts

        violation_counts: dict[str, int] = {}
        for cv in claim_verdicts:
            if cv.violation_code:
                violation_counts[cv.violation_code] = violation_counts.get(cv.violation_code, 0) + 1
        if grounding_report:
            for gv in grounding_report.verdicts:
                if gv.violation_code:
                    # Ensure any additional broken refs in the same claim are also counted if not already
                    pass

        total_claims = len(claim_verdicts)
        supported_claims = sum(1 for cv in claim_verdicts if cv.passed)
        failed_claims = total_claims - supported_claims
        grounding_ok = grounding_report.passed if grounding_report is not None else True
        passed = (failed_claims == 0) and grounding_ok

        overall_score = (supported_claims / total_claims) if total_claims > 0 else 1.0
        repair_prompt = (
            ""
            if passed
            else self.compiler.compile(
                claim_verdicts=claim_verdicts,
                grounding_report=grounding_report,
                sources=source_list,
            )
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        return VerificationResult(
            passed=passed,
            overall_score=overall_score,
            total_claims=total_claims,
            supported_claims=supported_claims,
            failed_claims=failed_claims,
            violation_counts=violation_counts,
            claim_verdicts=claim_verdicts,
            grounding_report=grounding_report,
            repair_prompt=repair_prompt,
            elapsed_ms=elapsed_ms,
        )
