"""Stage 4: Style and Best Practices Review.

Implements ADR-0023 Stage 4 and the 20-day plan Task 8.2.

Rules:
- Uses cheapest available model.
- Runs per-hunk independently (all parallel).
- Only low/medium severity.
- Skips test files.
- Skips hunks already covered by Stage 2 findings (±3 line overlap).
- Skipped entirely if config.review_style == False.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from app.core.comment_formatter import Finding
from app.core.diff_parser import FileDiff, build_line_to_position_map
from app.llm.ast_validator import INVALID_SUGGESTION_NOTE, validate_suggestion
from app.llm.prompts import PROMPT_STYLE_REVIEW, SYSTEM_REVIEWER

if TYPE_CHECKING:
    from app.pipeline.orchestrator import ReviewContext

logger = logging.getLogger(__name__)

_TEST_FILE_PATTERN = re.compile(
    r"(test_|_test\.|\.test\.|\.spec\.|__test__|/tests?/|/spec/)",
    re.IGNORECASE,
)

# Allowed severities for style findings
_STYLE_SEVERITIES = {"low", "medium"}


async def run_style_review(
    ctx: "ReviewContext",
    existing_findings: list[Finding],
) -> list[Finding]:
    """Run style review across all reviewable hunks.

    Args:
        ctx: The shared ``ReviewContext``.
        existing_findings: Findings already produced by Stage 2 (used to
                           skip overlapping hunks).

    Returns:
        List of style ``Finding`` objects.
    """
    if not ctx.config.style_review:
        logger.info("Stage 4: skipped (review.style=false in config)")
        return []

    semaphore = ctx.semaphore
    tasks: list[asyncio.Task[list[Finding]]] = []

    for file_diff in ctx.file_diffs:
        if _TEST_FILE_PATTERN.search(file_diff.filename):
            logger.debug("Stage 4: skipping test file %s", file_diff.filename)
            continue

        position_map = build_line_to_position_map(file_diff)

        for hunk in file_diff.hunks:
            # Skip if this hunk's line range is already covered by Stage 2
            if _is_covered(hunk, existing_findings, file_diff.filename):
                logger.debug(
                    "Stage 4: skipping hunk@%d in %s — covered by Stage 2",
                    hunk.new_start,
                    file_diff.filename,
                )
                continue

            task = asyncio.create_task(
                _analyze_hunk_style(
                    file_diff, hunk, position_map, ctx, semaphore
                )
            )
            tasks.append(task)

    if not tasks:
        return []

    results = await asyncio.gather(*tasks, return_exceptions=True)
    findings: list[Finding] = []
    for r in results:
        if isinstance(r, list):
            findings.extend(r)
        elif isinstance(r, Exception):
            logger.warning("Style review task failed: %s", r)

    logger.info("Stage 4 complete: %d style findings", len(findings))
    return findings


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


def _is_covered(
    hunk: object,
    existing: list[Finding],
    file_path: str,
    tolerance: int = 3,
) -> bool:
    """Return True if any existing finding overlaps with the hunk's range."""
    hunk_start = hunk.new_start
    hunk_end = hunk.new_start + hunk.new_count

    for f in existing:
        if f.file_path != file_path:
            continue
        if (
            f.line_start <= hunk_end + tolerance
            and f.line_end >= hunk_start - tolerance
        ):
            return True
    return False


async def _analyze_hunk_style(
    file_diff: FileDiff,
    hunk: object,
    position_map: dict[int, int],
    ctx: "ReviewContext",
    semaphore: asyncio.Semaphore,
) -> list[Finding]:
    async with semaphore:
        try:
            # Build hunk content
            parts: list[str] = []
            for dl in hunk.lines:
                prefix = "+" if dl.line_type == "added" else (" " if dl.line_type == "context" else "-")
                lineno = dl.new_lineno or dl.old_lineno or 0
                parts.append(f"{lineno:4d} {prefix} {dl.content}")
            hunk_content = "\n".join(parts)

            custom_guidelines = ctx.config.custom_guidelines or "(none)"

            prompt = PROMPT_STYLE_REVIEW.format(
                file_path=file_diff.filename,
                language=file_diff.language or "text",
                hunk_content=hunk_content,
                custom_guidelines=custom_guidelines,
            )

            data, _cost = await ctx.llm.complete_with_json(
                prompt,
                system=SYSTEM_REVIEWER,
            )

            if not isinstance(data, list):
                return []

            findings: list[Finding] = []
            for item in data:
                if not isinstance(item, dict):
                    continue

                severity = str(item.get("severity", "low")).lower()
                if severity not in _STYLE_SEVERITIES:
                    severity = "low"

                line_start = int(item.get("line_start") or 0)
                line_end = int(item.get("line_end") or line_start)

                diff_pos = position_map.get(line_start)
                if diff_pos is None:
                    for offset in range(1, 4):
                        diff_pos = position_map.get(line_start + offset)
                        if diff_pos:
                            break
                        diff_pos = position_map.get(line_start - offset)
                        if diff_pos:
                            break
                if diff_pos is None:
                    continue

                suggestion_code = item.get("suggestion_code")
                body = str(item.get("body", ""))
                if suggestion_code:
                    suggestion_code = str(suggestion_code).strip() or None
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

                findings.append(
                    Finding(
                        file_path=file_diff.filename,
                        line_start=line_start,
                        line_end=line_end,
                        diff_position=diff_pos,
                        severity=severity,
                        category="style",
                        title=str(item.get("title", "Style issue")),
                        body=body,
                        suggestion_code=suggestion_code,
                        confidence=0.7,
                    )
                )

            return findings

        except Exception:
            logger.exception(
                "Style review failed for %s hunk@%d",
                file_diff.filename,
                getattr(hunk, "new_start", 0),
            )
            return []
