"""Workspace Symbol Grounding Engine implementing rules TG006-TG008 via AST and filesystem inspection."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from truthgate.decomposer import ClaimDecomposer
from truthgate.models import AtomicClaim, ClaimVerdict, CodeReference, GroundingReport, VerdictStatus

_NON_PY_SYMBOL_PATTERNS: tuple[re.Pattern[str], ...] = (
    # TypeScript / JavaScript: function foo, class Bar, const baz =, export interface Qux
    re.compile(r"\b(?:export\s+)?(?:async\s+)?(?:function|class|interface|type|const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    # Go: func foo, func (r *Recv) foo, type Bar struct/interface
    re.compile(r"\bfunc\s+(?:\([^)]+\)\s+)?([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"\btype\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:struct|interface)"),
    # Rust: pub fn foo, struct Bar, enum Baz, trait Qux
    re.compile(r"\b(?:pub\s+)?(?:async\s+)?(?:fn|struct|enum|trait|mod)\s+([A-Za-z_][A-Za-z0-9_]*)"),
)


class WorkspaceSymbolGrounder:
    """Verifies file paths, #Lstart-Lend line ranges, and AST symbols against a workspace directory."""

    def __init__(self, decomposer: ClaimDecomposer | None = None) -> None:
        self.decomposer = decomposer or ClaimDecomposer()
        self._workspace_cache: dict[str, dict[str, list[str]]] = {}
        self._file_cache: dict[str, tuple[list[str], list[str]]] = {}
        self._resolved_roots: dict[str, Path] = {}

    def _resolve_root(self, workspace_root: str | Path) -> Path:
        raw = str(workspace_root)
        if raw not in self._resolved_roots:
            self._resolved_roots[raw] = Path(workspace_root).resolve()
        return self._resolved_roots[raw]

    def ground_text(self, text: str, workspace_root: str | Path) -> GroundingReport:
        """Extract all claims and code references from markdown text and verify against workspace_root."""
        claims = self.decomposer.decompose(text)
        return self.ground_claims(claims, workspace_root)

    def ground_claims(
        self, claims: list[AtomicClaim], workspace_root: str | Path
    ) -> GroundingReport:
        """Verify all CodeReferences across a list of AtomicClaims against workspace_root."""
        root = self._resolve_root(workspace_root)
        verdicts: list[ClaimVerdict] = []
        available_symbols: dict[str, list[str]] = {}

        total_refs = 0
        valid_refs = 0
        broken_refs = 0

        for claim in claims:
            if not claim.code_refs:
                continue
            claim_files = [r.file_path for r in claim.code_refs if r.file_path]
            default_file = claim_files[0] if len(claim_files) == 1 else None

            filtered_refs = self._deduplicate_refs(claim.code_refs, default_file)

            for ref_idx, ref in enumerate(filtered_refs, start=1):
                total_refs += 1
                ref_id = f"{claim.claim_id}_ref_{ref_idx}"
                verdict = self._verify_reference(
                    ref_id=ref_id,
                    claim_text=claim.raw_text,
                    ref=ref,
                    root=root,
                    available_symbols=available_symbols,
                )
                verdicts.append(verdict)
                if verdict.passed:
                    valid_refs += 1
                else:
                    broken_refs += 1

        return GroundingReport(
            workspace_root=str(root),
            total_references=total_refs,
            valid_references=valid_refs,
            broken_references=broken_refs,
            verdicts=verdicts,
            available_symbols=available_symbols,
        )

    def _deduplicate_refs(
        self, refs: list[CodeReference], default_file: str | None
    ) -> list[CodeReference]:
        """Bind unattached symbols to default_file and avoid duplicate checks of the bare file."""
        bound: list[CodeReference] = []
        for r in refs:
            fpath = r.file_path or default_file
            bound.append(
                CodeReference(
                    raw=r.raw,
                    file_path=fpath,
                    start_line=r.start_line,
                    end_line=r.end_line,
                    symbol=r.symbol,
                )
            )
        # If there is a bare file ref (file_path=X, start_line=None, symbol=None) AND another ref
        # with the same file_path that also checks line range or symbol, drop the redundant bare ref.
        specific_files = {
            r.file_path
            for r in bound
            if r.file_path and (r.start_line is not None or r.symbol is not None)
        }
        result: list[CodeReference] = []
        for r in bound:
            if (
                r.file_path in specific_files
                and r.start_line is None
                and r.symbol is None
                and len(bound) > 1
            ):
                continue
            result.append(r)
        return result

    def _verify_reference(
        self,
        ref_id: str,
        claim_text: str,
        ref: CodeReference,
        root: Path,
        available_symbols: dict[str, list[str]],
    ) -> ClaimVerdict:
        """Verify a single CodeReference against TG006, TG007, and TG008."""
        # Case 1: Symbol referenced without any file path
        if not ref.file_path and ref.symbol:
            workspace_index = self._get_workspace_index(root)
            all_syms: set[str] = set()
            for syms in workspace_index.values():
                all_syms.update(syms)
            if self._symbol_exists(ref.symbol, all_syms):
                return ClaimVerdict(
                    claim_id=ref_id,
                    claim_text=claim_text,
                    status=VerdictStatus.SUPPORTED.value,
                    confidence=1.0,
                    explanation=f"Symbol '{ref.symbol}' exists in workspace symbol index.",
                    matched_span=f"Symbol '{ref.symbol}' verified in workspace",
                    violation_code="",
                    source_id="workspace",
                )
            sample_syms = sorted(all_syms)[:12]
            return ClaimVerdict(
                claim_id=ref_id,
                claim_text=claim_text,
                status=VerdictStatus.BROKEN_CODE_REF.value,
                confidence=1.0,
                explanation=(
                    f"TG008 Phantom Code Symbol: Symbol '{ref.symbol}' does not exist in any "
                    f"workspace file (available workspace symbols: {sample_syms})."
                ),
                matched_span=f"Available symbols: {', '.join(sample_syms)}",
                violation_code="TG008",
                source_id="workspace",
            )

        assert ref.file_path is not None
        rel_path = ref.file_path.lstrip("/")
        norm_candidate = Path(os.path.normpath(os.path.join(str(root), rel_path)))

        # Security check: block path traversal outside workspace root
        try:
            norm_candidate.relative_to(root)
        except ValueError:
            return ClaimVerdict(
                claim_id=ref_id,
                claim_text=claim_text,
                status=VerdictStatus.BROKEN_CODE_REF.value,
                confidence=1.0,
                explanation=(
                    f"TG006 Non-Existent File Path: Path '{ref.file_path}' escapes workspace root."
                ),
                matched_span="",
                violation_code="TG006",
                source_id=ref.file_path,
            )

        cand_key = str(norm_candidate)
        if cand_key not in self._file_cache:
            # Rule TG006: Non-Existent File Path
            if not norm_candidate.exists() or not norm_candidate.is_file():
                workspace_index = self._get_workspace_index(root)
                existing_files = sorted(workspace_index.keys())[:10]
                return ClaimVerdict(
                    claim_id=ref_id,
                    claim_text=claim_text,
                    status=VerdictStatus.BROKEN_CODE_REF.value,
                    confidence=1.0,
                    explanation=(
                        f"TG006 Non-Existent File Path: File '{ref.file_path}' does not exist in "
                        f"workspace (available files: {existing_files})."
                    ),
                    matched_span=f"Available files: {', '.join(existing_files)}",
                    violation_code="TG006",
                    source_id=ref.file_path,
                )
            file_text = norm_candidate.read_text(encoding="utf-8", errors="replace")
            lines = file_text.splitlines()
            syms = sorted(self.extract_file_symbols(norm_candidate, file_text))
            self._file_cache[cand_key] = (lines, syms)
        else:
            lines, syms = self._file_cache[cand_key]

        line_count = len(lines)
        available_symbols[ref.file_path] = syms
        file_syms = set(syms)

        # Rule TG007: Out-of-Bounds Line Range
        if ref.start_line is not None:
            end_line = ref.end_line if ref.end_line is not None else ref.start_line
            if ref.start_line < 1 or end_line < ref.start_line or end_line > line_count:
                return ClaimVerdict(
                    claim_id=ref_id,
                    claim_text=claim_text,
                    status=VerdictStatus.BROKEN_CODE_REF.value,
                    confidence=1.0,
                    explanation=(
                        f"TG007 Out-of-Bounds Line Range: Referenced {ref.file_path}#L{ref.start_line}"
                        f"-L{end_line}, but '{ref.file_path}' only has {line_count} lines (valid range: L1-L{line_count})."
                    ),
                    matched_span=f"{ref.file_path} has {line_count} lines (L1-L{line_count})",
                    violation_code="TG007",
                    source_id=ref.file_path,
                )

        # Rule TG008: Phantom Code Symbol
        if ref.symbol:
            if not self._symbol_exists(ref.symbol, file_syms):
                sym_list = sorted(file_syms)
                return ClaimVerdict(
                    claim_id=ref_id,
                    claim_text=claim_text,
                    status=VerdictStatus.BROKEN_CODE_REF.value,
                    confidence=1.0,
                    explanation=(
                        f"TG008 Phantom Code Symbol: Symbol '{ref.symbol}' does not exist in "
                        f"'{ref.file_path}' AST (available symbols in {ref.file_path}: {sym_list})."
                    ),
                    matched_span=f"Available symbols in {ref.file_path}: {', '.join(sym_list)}",
                    violation_code="TG008",
                    source_id=ref.file_path,
                )

        # Reference is completely grounded!
        span_preview = f"{ref.file_path} (lines: 1..{line_count})"
        if ref.start_line is not None:
            end_line = ref.end_line or ref.start_line
            snippet = " ".join(lines[ref.start_line - 1 : min(end_line, ref.start_line + 2)]).strip()
            span_preview = f"{ref.file_path}#L{ref.start_line}-L{end_line}: {snippet[:120]}"
        elif ref.symbol:
            span_preview = f"{ref.file_path} defines '{ref.symbol}'"

        return ClaimVerdict(
            claim_id=ref_id,
            claim_text=claim_text,
            status=VerdictStatus.SUPPORTED.value,
            confidence=1.0,
            explanation=f"Workspace reference '{ref.raw}' verified against '{ref.file_path}'.",
            matched_span=span_preview,
            violation_code="",
            source_id=ref.file_path,
        )

    @staticmethod
    def _symbol_exists(target: str, file_symbols: set[str]) -> bool:
        """Check whether target symbol or unqualified method matches file_symbols."""
        if target in file_symbols:
            return True
        # If target is unqualified method name, check if Class.target exists
        for sym in file_symbols:
            if sym.endswith(f".{target}"):
                return True
        return False

    def extract_file_symbols(self, file_path: Path, content: str | None = None) -> set[str]:
        """Extract AST symbols for Python files or regex symbol tables for TS/JS/Go/Rust files."""
        text = content if content is not None else file_path.read_text(encoding="utf-8", errors="replace")
        suffix = file_path.suffix.lower()
        symbols: set[str] = set()

        if suffix == ".py":
            try:
                tree = ast.parse(text, filename=str(file_path))
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbols.add(node.name)
                    elif isinstance(node, ast.ClassDef):
                        symbols.add(node.name)
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                symbols.add(item.name)
                                symbols.add(f"{node.name}.{item.name}")
                    elif isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                symbols.add(target.id)
                    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                        symbols.add(node.target.id)
                return symbols
            except SyntaxError:
                pass

        for pattern in _NON_PY_SYMBOL_PATTERNS:
            for match in pattern.finditer(text):
                symbols.add(match.group(1))
        return symbols

    def _get_workspace_index(self, root: Path) -> dict[str, list[str]]:
        key = str(root)
        if key not in self._workspace_cache:
            self._workspace_cache[key] = self._index_workspace_symbols(root) if root.exists() else {}
        return self._workspace_cache[key]

    def _index_workspace_symbols(self, root: Path) -> dict[str, list[str]]:
        """Build a relative-path -> symbols index for all source files in workspace_root."""
        index: dict[str, list[str]] = {}
        allowed_exts = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb", ".sql", ".yaml", ".yml", ".json", ".toml", ".md", ".sh"}
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part.startswith(".") or part in {"__pycache__", "node_modules", "venv", ".venv"} for part in path.parts):
                continue
            if path.suffix.lower() not in allowed_exts:
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            text = path.read_text(encoding="utf-8", errors="replace")
            syms = sorted(self.extract_file_symbols(path, text))
            self._file_cache[str(path.resolve())] = (text.splitlines(), syms)
            index[rel] = syms
        return index
