"""Stage 1: PR Summarization.

Implements ADR-0023 Stage 1 and the 20-day plan Task 7.2.

Produces a concise PR summary and risk-level classification using the
cheapest available LLM tier.  The summary is:
1. Injected into all subsequent stage prompts for cross-file coherence.
2. Posted as the opening paragraph of the top-level PR comment.

Uses only the first 3 000 characters of the diff to keep costs minimal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.llm.client import LLMClient
from app.llm.prompts import PROMPT_SUMMARIZE, SYSTEM_REVIEWER

logger = logging.getLogger(__name__)

DIFF_PREVIEW_CHARS = 3_000


@dataclass
class SummaryResult:
    """Result of the PR summarization stage."""

    summary: str
    key_changes: list[str] = field(default_factory=list)
    risk_level: str = "low"   # "low" | "medium" | "high"
    cost_usd: float = 0.0


async def run_summarization(
    diff_text: str,
    pr_title: str,
    pr_description: str,
    llm_client: LLMClient,
) -> SummaryResult:
    """Summarize the PR and classify its risk level.

    Args:
        diff_text: Raw unified diff text (will be truncated to 3 000 chars).
        pr_title: Pull request title.
        pr_description: Pull request body / description.
        llm_client: Initialized LLM client.

    Returns:
        ``SummaryResult`` with summary text, key changes list, risk level,
        and cost.
    """
    diff_preview = diff_text[:DIFF_PREVIEW_CHARS]

    prompt = PROMPT_SUMMARIZE.format(
        pr_title=pr_title or "(no title)",
        pr_description=pr_description or "(no description)",
        diff_summary=diff_preview,
    )

    try:
        data, cost = await llm_client.complete_with_json(
            prompt,
            system=SYSTEM_REVIEWER,
        )

        if not isinstance(data, dict):
            logger.warning("Summarization returned unexpected type: %s", type(data))
            return _fallback_result(cost)

        summary = str(data.get("summary", "PR reviewed."))
        key_changes = data.get("key_changes", [])
        if not isinstance(key_changes, list):
            key_changes = []
        key_changes = [str(c) for c in key_changes]

        risk_raw = str(data.get("risk_level", "low")).lower()
        risk_level = risk_raw if risk_raw in ("low", "medium", "high") else "low"

        logger.info(
            "Summarization complete",
            extra={"risk_level": risk_level, "cost_usd": f"{cost:.6f}"},
        )

        return SummaryResult(
            summary=summary,
            key_changes=key_changes,
            risk_level=risk_level,
            cost_usd=cost,
        )

    except Exception:
        logger.exception("Summarization failed — returning fallback result")
        return _fallback_result(0.0)


def _fallback_result(cost: float) -> SummaryResult:
    return SummaryResult(
        summary="PR reviewed by OpenRabbit.",
        key_changes=[],
        risk_level="low",
        cost_usd=cost,
    )
