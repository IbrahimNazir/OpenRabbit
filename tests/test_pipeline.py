"""Tests for the pipeline orchestrator — end-to-end with mocked GitHub + LLM."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.comment_formatter import ReviewResult


# ---------------------------------------------------------------------------
#  Sample diff for testing
# ---------------------------------------------------------------------------

SAMPLE_DIFF = """\
diff --git a/app/utils.py b/app/utils.py
index abc1234..def5678 100644
--- a/app/utils.py
+++ b/app/utils.py
@@ -10,6 +10,8 @@ def process_data(items):
     results = []
     for item in items:
         if item.is_valid():
+            # BUG: no null check on item.value
+            result = item.value.strip()
             results.append(result)
     return results
"""


# ---------------------------------------------------------------------------
#  Tests
# ---------------------------------------------------------------------------


class TestRunPipeline:
    """Tests for the full pipeline execution."""

    @pytest.mark.asyncio
    async def test_pipeline_returns_result(self):
        """Pipeline returns a ReviewResult with findings."""
        from app.pipeline.orchestrator import run_pipeline

        mock_github = MagicMock()
        mock_github.get_pr_diff = AsyncMock(return_value=SAMPLE_DIFF)

        llm_findings = json.dumps([
            {
                "line_start": 13,
                "line_end": 14,
                "severity": "high",
                "category": "bug",
                "title": "Missing null check",
                "body": "item.value could be None",
                "suggestion_code": "if item.value:\n    result = item.value.strip()",
            }
        ])

        with patch("app.pipeline.orchestrator.LLMClient") as MockLLM:
            mock_llm = MagicMock()
            mock_llm.complete_with_json = AsyncMock(
                side_effect=[
                    # Summary call
                    ({"summary": "Adds data processing", "key_changes": ["processing"], "risk_level": "low"}, 0.001),
                    # Bug detection call
                    (json.loads(llm_findings), 0.005),
                ]
            )
            MockLLM.return_value = mock_llm

            result = await run_pipeline(
                github_client=mock_github,
                repo_full_name="test/repo",
                pr_number=1,
                head_sha="abc123",
                base_sha="def456",
                pr_title="Fix data processing",
            )

        assert isinstance(result, ReviewResult)
        assert result.pr_summary == "Adds data processing"
        assert result.files_reviewed == 1
        assert result.total_cost_usd > 0

    @pytest.mark.asyncio
    async def test_pipeline_handles_empty_diff(self):
        """Pipeline handles empty diff gracefully."""
        from app.pipeline.orchestrator import run_pipeline

        mock_github = MagicMock()
        mock_github.get_pr_diff = AsyncMock(return_value="")

        result = await run_pipeline(
            github_client=mock_github,
            repo_full_name="test/repo",
            pr_number=1,
            head_sha="abc123",
            base_sha="def456",
        )

        assert isinstance(result, ReviewResult)
        assert "No code changes" in result.pr_summary

    @pytest.mark.asyncio
    async def test_pipeline_handles_diff_fetch_failure(self):
        """Pipeline handles GitHub API failure gracefully."""
        from app.pipeline.orchestrator import run_pipeline

        mock_github = MagicMock()
        mock_github.get_pr_diff = AsyncMock(side_effect=Exception("Network error"))

        result = await run_pipeline(
            github_client=mock_github,
            repo_full_name="test/repo",
            pr_number=1,
            head_sha="abc123",
            base_sha="def456",
        )

        assert "Failed" in result.pr_summary

    @pytest.mark.asyncio
    async def test_pipeline_skips_noncode_files(self):
        """Pipeline skips markdown and lockfiles."""
        from app.pipeline.orchestrator import run_pipeline

        md_diff = """\
diff --git a/README.md b/README.md
index abc1234..def5678 100644
--- a/README.md
+++ b/README.md
@@ -1,3 +1,4 @@
 # Project
+New line added
 Description
"""
        mock_github = MagicMock()
        mock_github.get_pr_diff = AsyncMock(return_value=md_diff)

        result = await run_pipeline(
            github_client=mock_github,
            repo_full_name="test/repo",
            pr_number=1,
            head_sha="abc123",
            base_sha="def456",
        )

        assert "non-code" in result.pr_summary.lower() or "skipping" in result.pr_summary.lower()
        assert len(result.findings) == 0
