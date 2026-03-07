"""Stage 5: Synthesis and Deduplication.

Implements ADR-0026 (two-step deduplication) and the 20-day plan Task 9.2.

Step 1 — Rule-based (no LLM):
  - Group by file + overlapping line range (within 3 lines)
  - Keep highest severity/confidence in each group
  - Remove diff_position=None findings
  - Remove below severity_threshold
  - Cap at 25

Step 2 — LLM dedup (only if > 15 findings remain after Step 1):
  - PROMPT_SYNTHESIS via cheap model
  - Parse "keep" list, apply filter

Final sort: severity_order → file_path → line_start
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.core.comment_formatter import Finding
from app.llm.prompts import PROMPT_SYNTHESIS, SYSTEM_REVIEWER

if TYPE_CHECKING:
    from app.pipeline.orchestrator import ReviewContext

logger = logging.getLogger(__name__)

MAX_FINDINGS = 25
LLM_DEDUP_THRESHOLD = 15

SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}

SEVERITY_THRESHOLD_ORDER: dict[str, int] = SEVERITY_ORDER


async def run_synthesis(
    all_findings: list[Finding],
    ctx: "ReviewContext",
) -> list[Finding]:
    """Deduplicate and synthesize findings.

    Args:
        all_findings: Raw findings from stages 0–4.
        ctx: The shared ``ReviewContext``.

    Returns:
        Deduplicated, sorted findings list.
    """
    if not all_findings:
        return []

    logger.info("Stage 5: starting with %d raw findings", len(all_findings))

    # Step 1: Rule-based dedup
    findings = _rule_based_dedup(all_findings, ctx)

    logger.info(
        "Stage 5: %d findings after rule-based dedup", len(findings)
    )

    # Step 2: LLM dedup (only if many findings remain)
    if len(findings) > LLM_DEDUP_THRESHOLD:
        findings = await _llm_dedup(findings, ctx)
        logger.info("Stage 5: %d findings after LLM dedup", len(findings))

    # Final sort
    findings.sort(
        key=lambda f: (
            SEVERITY_ORDER.get(f.severity, 5),
            f.file_path,
            f.line_start,
        )
    )

    logger.info("Stage 5 complete: %d final findings", len(findings))
    return findings


# ---------------------------------------------------------------------------
#  Rule-based deduplication (Step 1)
# ---------------------------------------------------------------------------


def _rule_based_dedup(
    findings: list[Finding],
    ctx: "ReviewContext",
) -> list[Finding]:
    """Apply deterministic deduplication rules."""
    # Filter: must have a valid diff_position
    findings = [f for f in findings if f.diff_position is not None and f.diff_position > 0]

    # Filter: meet severity threshold
    threshold = ctx.config.severity_threshold
    threshold_order = SEVERITY_THRESHOLD_ORDER.get(threshold, 3)
    findings = [
        f for f in findings
        if SEVERITY_ORDER.get(f.severity, 5) <= threshold_order
    ]

    # Group by file + overlapping line range
    groups: list[list[Finding]] = []
    used: set[int] = set()

    for i, finding in enumerate(findings):
        if i in used:
            continue
        group = [finding]
        used.add(i)

        for j in range(i + 1, len(findings)):
            if j in used:
                continue
            other = findings[j]
            if other.file_path != finding.file_path:
                continue
            if _ranges_overlap(
                finding.line_start,
                finding.line_end,
                other.line_start,
                other.line_end,
                tolerance=3,
            ):
                group.append(other)
                used.add(j)

        groups.append(group)

    # Keep best from each group
    deduped: list[Finding] = []
    for group in groups:
        best = max(
            group,
            key=lambda f: (
                -SEVERITY_ORDER.get(f.severity, 5),  # higher = better (negate)
                f.confidence,
            ),
        )
        deduped.append(best)

    # Cap at MAX_FINDINGS (keep highest severity)
    deduped.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 5))
    return deduped[:MAX_FINDINGS]


def _ranges_overlap(
    start1: int, end1: int, start2: int, end2: int, tolerance: int = 3
) -> bool:
    """Return True if two line ranges overlap (with tolerance)."""
    return start1 <= end2 + tolerance and end1 >= start2 - tolerance


# ---------------------------------------------------------------------------
#  LLM deduplication (Step 2)
# ---------------------------------------------------------------------------


async def _llm_dedup(
    findings: list[Finding],
    ctx: "ReviewContext",
) -> list[Finding]:
    """Use LLM to remove false positives and duplicates."""
    # Build JSON representation with indices
    findings_json = json.dumps(
        [
            {
                "id": i,
                "file": f.file_path,
                "line": f.line_start,
                "severity": f.severity,
                "category": f.category,
                "title": f.title,
                "body": f.body[:200],  # truncate for token efficiency
            }
            for i, f in enumerate(findings)
        ],
        indent=2,
    )

    summary_text = ctx.summary.summary if ctx.summary else "PR reviewed."

    prompt = PROMPT_SYNTHESIS.format(
        pr_summary=summary_text,
        all_findings_json=findings_json,
    )

    try:
        data, _cost = await ctx.llm.complete_with_json(
            prompt,
            system=SYSTEM_REVIEWER,
        )

        if not isinstance(data, dict):
            return findings

        keep_ids = data.get("keep", [])
        if not isinstance(keep_ids, list):
            return findings

        # Convert to set of ints for fast lookup
        keep_set = set()
        for kid in keep_ids:
            try:
                keep_set.add(int(kid))
            except (TypeError, ValueError):
                pass

        if not keep_set:
            # LLM returned empty keep list — don't trust it, return all
            logger.warning("Stage 5 LLM returned empty keep list — ignoring")
            return findings

        kept = [f for i, f in enumerate(findings) if i in keep_set]

        # Safety net: if LLM tries to remove too many, cap at 50% reduction
        if len(kept) < len(findings) // 2:
            logger.warning(
                "Stage 5 LLM removed > 50%% of findings — capping at 50%% reduction"
            )
            kept = findings[: max(LLM_DEDUP_THRESHOLD, len(findings) // 2)]

        return kept

    except Exception:
        logger.exception("Stage 5 LLM dedup failed — returning rule-based results")
        return findings
