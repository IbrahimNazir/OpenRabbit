"""Comment formatting utilities for GitHub PR reviews.

Formats Finding objects into GitHub-compatible markdown comments,
including suggestion blocks and the review summary table.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# =============================================================================
#  Data classes
# =============================================================================


@dataclass
class Finding:
    """A single code review finding to be posted as an inline comment."""

    file_path: str
    line_start: int
    line_end: int
    diff_position: int
    severity: str  # critical | high | medium | low | info
    category: str  # bug | security | style | performance | logic
    title: str
    body: str
    suggestion_code: str | None = None
    confidence: float = 0.8


@dataclass
class ReviewResult:
    """Aggregate result of a full PR review pipeline run."""

    pr_summary: str
    findings: list[Finding] = field(default_factory=list)
    total_cost_usd: float = 0.0
    stages_completed: list[str] = field(default_factory=list)
    files_reviewed: int = 0
    hunks_reviewed: int = 0


# =============================================================================
#  Severity rendering
# =============================================================================

SEVERITY_EMOJI: dict[str, str] = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
    "info": "ℹ️",
}

SEVERITY_LABELS: dict[str, str] = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "info": "Info",
}


# =============================================================================
#  Inline comment formatting
# =============================================================================


def format_finding_comment(finding: Finding) -> str:
    """Format a Finding as a GitHub inline comment body.

    If a suggestion_code is present, wraps it in a GitHub suggestion block
    so the developer gets a one-click "Apply suggestion" button.
    """
    parts: list[str] = []

    # Header with severity badge
    emoji = SEVERITY_EMOJI.get(finding.severity, "❓")
    parts.append(f"**{emoji} {finding.title}** ({finding.severity})")
    parts.append("")
    parts.append(finding.body)

    # Code suggestion block
    if finding.suggestion_code:
        parts.append("")
        parts.append("```suggestion")
        parts.append(finding.suggestion_code)
        parts.append("```")

    return "\n".join(parts)


# =============================================================================
#  Summary comment formatting
# =============================================================================


def format_summary_comment(result: ReviewResult) -> str:
    """Format the top-level PR review summary as a GitHub markdown comment.

    Produces a severity table with counts and a footer with stats.
    """
    parts: list[str] = []

    parts.append("## 🐇 OpenRabbit AI Review")
    parts.append("")

    # Summary paragraph
    if result.pr_summary:
        parts.append(f"**Summary:** {result.pr_summary}")
        parts.append("")

    # Severity count table
    severity_counts: dict[str, int] = {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
    }
    for f in result.findings:
        if f.severity in severity_counts:
            severity_counts[f.severity] += 1

    parts.append("| Severity | Count |")
    parts.append("|----------|-------|")
    for sev, count in severity_counts.items():
        emoji = SEVERITY_EMOJI[sev]
        label = SEVERITY_LABELS[sev]
        parts.append(f"| {emoji} {label} | {count} |")

    parts.append("")

    # Footer with stats
    cost_str = f"${result.total_cost_usd:.4f}"
    parts.append(
        f"> Reviewed {result.files_reviewed} files, "
        f"{result.hunks_reviewed} hunks | "
        f"Cost: {cost_str} | "
        f"Stages: {', '.join(result.stages_completed)}"
    )

    return "\n".join(parts)
