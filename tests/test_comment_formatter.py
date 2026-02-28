"""Tests for the comment formatter — inline comments and summary table."""

from __future__ import annotations

from app.core.comment_formatter import (
    Finding,
    ReviewResult,
    format_finding_comment,
    format_summary_comment,
)


class TestFormatFindingComment:
    """Tests for inline comment formatting."""

    def test_basic_finding(self):
        finding = Finding(
            file_path="app/main.py",
            line_start=10,
            line_end=10,
            diff_position=5,
            severity="high",
            category="bug",
            title="Null pointer dereference",
            body="Variable `user` may be None when accessed.",
        )
        result = format_finding_comment(finding)

        assert "Null pointer dereference" in result
        assert "high" in result
        assert "🟠" in result
        assert "may be None" in result

    def test_finding_with_suggestion(self):
        finding = Finding(
            file_path="app/main.py",
            line_start=10,
            line_end=10,
            diff_position=5,
            severity="medium",
            category="bug",
            title="Missing check",
            body="Add a null check.",
            suggestion_code="if user is not None:\n    process(user)",
        )
        result = format_finding_comment(finding)

        assert "```suggestion" in result
        assert "if user is not None:" in result

    def test_finding_without_suggestion(self):
        finding = Finding(
            file_path="app/main.py",
            line_start=10,
            line_end=10,
            diff_position=5,
            severity="low",
            category="style",
            title="Naming",
            body="Consider a more descriptive name.",
        )
        result = format_finding_comment(finding)

        assert "```suggestion" not in result


class TestFormatSummaryComment:
    """Tests for the summary comment table."""

    def test_empty_review(self):
        result_obj = ReviewResult(
            pr_summary="No issues found.",
            findings=[],
            total_cost_usd=0.001,
            stages_completed=["filter", "summarize"],
            files_reviewed=3,
            hunks_reviewed=5,
        )
        summary = format_summary_comment(result_obj)

        assert "🐇 OpenRabbit AI Review" in summary
        assert "No issues found." in summary
        assert "| 🔴 Critical | 0 |" in summary
        assert "$0.0010" in summary

    def test_review_with_findings(self):
        findings = [
            Finding("a.py", 1, 1, 1, "critical", "security", "SQL injection", "Bad"),
            Finding("b.py", 5, 5, 3, "high", "bug", "NPE", "Null check"),
            Finding("b.py", 10, 10, 7, "high", "bug", "OOB", "Index error"),
            Finding("c.py", 2, 2, 2, "medium", "style", "Naming", "Rename"),
        ]
        result_obj = ReviewResult(
            pr_summary="Adds user auth.",
            findings=findings,
            total_cost_usd=0.05,
            stages_completed=["filter", "summarize", "bug_detection"],
            files_reviewed=3,
            hunks_reviewed=5,
        )
        summary = format_summary_comment(result_obj)

        assert "| 🔴 Critical | 1 |" in summary
        assert "| 🟠 High | 2 |" in summary
        assert "| 🟡 Medium | 1 |" in summary
