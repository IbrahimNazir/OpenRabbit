"""Stage 2: Bug and Security Detection.

Implements ADR-0023 Stage 2 and the 20-day plan Task 8.1.

Routing logic per file:
- Security-critical file (auth/payment/crypto patterns) → FILE_LEVEL with main model
- File with > 3 changed hunks → FILE_LEVEL
- Otherwise → HUNK_LEVEL (one LLM call per hunk, cheap model)

All file analyses run in parallel via asyncio.gather() + Semaphore(5).
Max 20 LLM calls total — additional files are truncated.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from app.core.comment_formatter import Finding
from app.core.diff_parser import FileDiff, build_line_to_position_map
from app.llm.ast_validator import INVALID_SUGGESTION_NOTE, validate_suggestion
from app.llm.prompts import PROMPT_BUG_DETECTION, SYSTEM_REVIEWER
from app.pipeline.stage_0_linters import LinterFinding

if TYPE_CHECKING:
    from app.pipeline.orchestrator import ReviewContext
    from app.rag.context_builder import EnrichedContext

logger = logging.getLogger(__name__)

# Maximum total LLM calls across all files in this stage
MAX_LLM_CALLS = 20

# File path patterns that trigger FILE_LEVEL analysis
_SECURITY_PATTERNS = re.compile(
    r"(auth|password|passwd|token|payment|billing|crypto|secret|jwt|oauth|"
    r"credential|session|permission|rbac|acl|signature|hmac)",
    re.IGNORECASE,
)

# Severity/confidence constants
CONFIDENCE_SECURITY = 0.9
CONFIDENCE_BUG = 0.8


async def run_bug_detection(ctx: "ReviewContext") -> list[Finding]:
    """Run bug and security detection across all reviewable files.

    Args:
        ctx: The shared ``ReviewContext`` object.

    Returns:
        List of ``Finding`` objects.
    """
    semaphore = ctx.semaphore
    llm = ctx.llm

    # Build linter findings index: file_path → list[LinterFinding]
    linter_by_file: dict[str, list[LinterFinding]] = {}
    for lf in ctx.linter_findings:
        linter_by_file.setdefault(lf.tool and lf.rule and lf.message and lf.line and lf.severity and lf.tool or "unknown", [])
    # Rebuild properly
    linter_by_file = {}
    for lf in ctx.linter_findings:
        # LinterFinding doesn't have a file_path — it's per-file already
        # (we attach it when building ctx.linter_findings in the orchestrator)
        pass

    tasks: list[asyncio.Task[list[Finding]]] = []
    call_count = 0

    for file_diff in ctx.file_diffs:
        if call_count >= MAX_LLM_CALLS:
            logger.info("Truncating bug detection — reached MAX_LLM_CALLS=%d", MAX_LLM_CALLS)
            break

        is_security_critical = bool(_SECURITY_PATTERNS.search(file_diff.filename))
        hunk_count = len(file_diff.hunks)
        use_file_level = is_security_critical or hunk_count > 3

        # Get linter findings for this file (passed through ctx)
        file_linter_findings = ctx.linter_findings_by_file.get(file_diff.filename, [])

        if use_file_level:
            if call_count < MAX_LLM_CALLS:
                task = asyncio.create_task(
                    _analyze_file_level(file_diff, file_linter_findings, ctx, semaphore)
                )
                tasks.append(task)
                call_count += 1
        else:
            for hunk in file_diff.hunks:
                if call_count >= MAX_LLM_CALLS:
                    break
                task = asyncio.create_task(
                    _analyze_hunk_level(
                        file_diff, hunk, file_linter_findings, ctx, semaphore
                    )
                )
                tasks.append(task)
                call_count += 1

    if not tasks:
        return []

    results = await asyncio.gather(*tasks, return_exceptions=True)
    findings: list[Finding] = []
    for r in results:
        if isinstance(r, list):
            findings.extend(r)
        elif isinstance(r, Exception):
            logger.warning("Bug detection task failed: %s", r)

    logger.info(
        "Stage 2 complete",
        extra={"findings": len(findings), "llm_calls": call_count},
    )
    return findings


# ---------------------------------------------------------------------------
#  Analysis helpers
# ---------------------------------------------------------------------------


def _format_rag_context(enriched: "EnrichedContext") -> str:
    """Format RAG-retrieved context as a markdown section for injection into prompts."""
    parts: list[str] = []

    if enriched.relevant_chunks:
        parts.append("## Semantically Related Code in This Repository")
        for chunk in enriched.relevant_chunks[:3]:
            parts.append(
                f"**{chunk.file_path}:{chunk.start_line}–{chunk.end_line}** "
                f"`{chunk.name}` (similarity {chunk.score:.2f}):\n"
                f"```{chunk.language}\n{chunk.content[:400]}\n```"
            )

    if enriched.caller_chunks:
        parts.append("## Call Sites of Changed Functions")
        for chunk in enriched.caller_chunks[:3]:
            parts.append(
                f"**{chunk.file_path}:{chunk.start_line}** `{chunk.name}`:\n"
                f"```{chunk.language}\n{chunk.content[:300]}\n```"
            )

    if enriched.past_findings:
        parts.append("## Similar Past Findings (Few-Shot Examples)")
        for pf in enriched.past_findings:
            parts.append(f"- **[{pf.severity}]** {pf.title}: {pf.body[:150]}")

    return "\n\n".join(parts)


def _build_linter_context(linter_findings: list[LinterFinding]) -> str:
    """Format linter findings as context for the LLM prompt."""
    if not linter_findings:
        return ""
    lines = ["**Static analysis findings for this file:**"]
    for lf in linter_findings:
        lines.append(f"  - Line {lf.line} [{lf.tool}/{lf.rule}]: {lf.message}")
    return "\n".join(lines)


def _build_hunk_content(file_diff: FileDiff, hunk_lines_to_use: list) -> str:
    """Format hunk lines with line numbers and +/- prefixes."""
    parts: list[str] = []
    for dl in hunk_lines_to_use:
        prefix = "+" if dl.line_type == "added" else ("-" if dl.line_type == "removed" else " ")
        lineno = dl.new_lineno or dl.old_lineno or 0
        parts.append(f"{lineno:4d} {prefix} {dl.content}")
    return "\n".join(parts)


def _parse_findings_from_json(
    data: object,
    file_diff: FileDiff,
    position_map: dict[int, int],
) -> list[Finding]:
    """Convert raw LLM JSON response to Finding objects."""
    if not isinstance(data, list):
        return []

    findings: list[Finding] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        line_start = int(item.get("line_start") or 0)
        line_end = int(item.get("line_end") or line_start)
        severity = str(item.get("severity", "low")).lower()
        category = str(item.get("category", "bug")).lower()

        # Map line_start to diff_position
        diff_pos = _resolve_position(line_start, position_map)
        if diff_pos is None:
            logger.debug(
                "Skipping finding at line %d — no diff position", line_start
            )
            continue

        title = str(item.get("title", "Issue found"))
        body = str(item.get("body", ""))
        suggestion_code = item.get("suggestion_code")
        if suggestion_code:
            suggestion_code = str(suggestion_code).strip() or None

        # Validate suggestion with AST
        if suggestion_code:
            vr = validate_suggestion(
                suggestion_code,
                file_diff.language or "text",
                line_start=line_start,
                line_end=line_end,
            )
            if not vr.is_valid:
                body = body + "\n\n" + INVALID_SUGGESTION_NOTE
                suggestion_code = None
            elif vr.fixed_code:
                suggestion_code = vr.fixed_code

        confidence = CONFIDENCE_SECURITY if category == "security" else CONFIDENCE_BUG

        findings.append(
            Finding(
                file_path=file_diff.filename,
                line_start=line_start,
                line_end=line_end,
                diff_position=diff_pos,
                severity=severity,
                category=category,
                title=title,
                body=body,
                suggestion_code=suggestion_code,
                confidence=confidence,
            )
        )

    return findings


def _resolve_position(line: int, position_map: dict[int, int]) -> int | None:
    """Resolve a new-file line number to a diff position, with ±3 tolerance."""
    if line in position_map:
        return position_map[line]
    for offset in range(1, 4):
        if (line + offset) in position_map:
            return position_map[line + offset]
        if (line - offset) in position_map:
            return position_map[line - offset]
    return None


# ---------------------------------------------------------------------------
#  FILE_LEVEL analysis
# ---------------------------------------------------------------------------


async def _analyze_file_level(
    file_diff: FileDiff,
    linter_findings: list[LinterFinding],
    ctx: "ReviewContext",
    semaphore: asyncio.Semaphore,
) -> list[Finding]:
    """Analyze the entire file in one LLM call."""
    async with semaphore:
        try:
            full_file = file_diff.hunks[0].full_file_content if file_diff.hunks else ""
            if not full_file:
                # Fallback: concatenate all hunk lines
                all_lines: list[str] = []
                for hunk in file_diff.hunks:
                    all_lines.extend(_build_hunk_content(file_diff, hunk.lines).splitlines())
                full_file = "\n".join(all_lines)

            linter_context = _build_linter_context(linter_findings)
            summary_text = ctx.summary.summary if ctx.summary else ""

            full_file_context = ""
            if summary_text:
                full_file_context = f"**PR Summary:** {summary_text}\n\n"
            if linter_context:
                full_file_context += linter_context

            # Prepend RAG context when available (Phase 3)
            enriched = ctx.enriched_contexts.get(file_diff.filename)
            if enriched:
                rag_section = _format_rag_context(enriched)
                if rag_section:
                    full_file_context = rag_section + "\n\n---\n\n" + full_file_context

            prompt = PROMPT_BUG_DETECTION.format(
                file_path=file_diff.filename,
                language=file_diff.language or "text",
                hunk_content=full_file[:8000],  # cap context
                full_file_context=full_file_context,
            )

            data, cost = await ctx.llm.complete_with_json(
                prompt,
                system=SYSTEM_REVIEWER,
            )

            position_map = build_line_to_position_map(file_diff)
            findings = _parse_findings_from_json(data, file_diff, position_map)

            logger.debug(
                "FILE_LEVEL analysis: %s → %d findings (cost $%.4f)",
                file_diff.filename,
                len(findings),
                cost,
            )
            return findings

        except Exception:
            logger.exception("FILE_LEVEL analysis failed for %s", file_diff.filename)
            return []


# ---------------------------------------------------------------------------
#  HUNK_LEVEL analysis
# ---------------------------------------------------------------------------


async def _analyze_hunk_level(
    file_diff: FileDiff,
    hunk: object,
    linter_findings: list[LinterFinding],
    ctx: "ReviewContext",
    semaphore: asyncio.Semaphore,
) -> list[Finding]:
    """Analyze a single hunk in isolation."""
    async with semaphore:
        try:
            hunk_content = _build_hunk_content(file_diff, hunk.lines)

            # Prefer scope_context (AST-enriched) if available
            scope_ctx = hunk.scope_context or hunk_content

            linter_context = _build_linter_context(linter_findings)
            summary_text = ctx.summary.summary if ctx.summary else ""

            full_file_context_parts: list[str] = []

            # Prepend RAG context when available (Phase 3)
            enriched = ctx.enriched_contexts.get(file_diff.filename)
            if enriched:
                rag_section = _format_rag_context(enriched)
                if rag_section:
                    full_file_context_parts.append(rag_section)
                    full_file_context_parts.append("---")

            if summary_text:
                full_file_context_parts.append(f"**PR Summary:** {summary_text}")
            if hunk.ast_function_context:
                full_file_context_parts.append(
                    f"**Enclosing function:** `{hunk.ast_function_context}`"
                )
            if linter_context:
                full_file_context_parts.append(linter_context)
            if scope_ctx != hunk_content:
                full_file_context_parts.append(
                    f"**Surrounding context:**\n```\n{scope_ctx}\n```"
                )

            full_file_context = "\n\n".join(full_file_context_parts)

            prompt = PROMPT_BUG_DETECTION.format(
                file_path=file_diff.filename,
                language=file_diff.language or "text",
                hunk_content=hunk_content,
                full_file_context=full_file_context,
            )

            data, cost = await ctx.llm.complete_with_json(
                prompt,
                system=SYSTEM_REVIEWER,
            )

            position_map = build_line_to_position_map(file_diff)
            findings = _parse_findings_from_json(data, file_diff, position_map)

            logger.debug(
                "HUNK_LEVEL analysis: %s hunk@%d → %d findings (cost $%.4f)",
                file_diff.filename,
                hunk.new_start,
                len(findings),
                cost,
            )
            return findings

        except Exception:
            logger.exception(
                "HUNK_LEVEL analysis failed for %s hunk@%d",
                file_diff.filename,
                getattr(hunk, "new_start", 0),
            )
            return []
