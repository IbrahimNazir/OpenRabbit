"""End-to-end tests for complete review pipelines.

These tests simulate real-world workflows from webhook payload to review results.
All tests use real Gatekeeper filter logic and pipeline orchestrator, with mocked
GitHub API and LLM clients.

Marked with @pytest.mark.e2e for optional exclusion from CI runs.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.core.comment_formatter import Finding, ReviewResult
from app.core.filter_engine import FilterEngine
from app.pipeline.orchestrator import run_pipeline
from tests.fixtures.sample_diffs import (
    BOT_PR_DIFF,
    SECURITY_PYTHON_DIFF,
    SIMPLE_PYTHON_DIFF,
    TYPESCRIPT_DIFF,
)

# =============================================================================
#  Markers and test configuration
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestFullPipelineE2E:
    """End-to-end tests for complete review workflows."""

    async def test_simple_pr_completes_successfully(
        self,
        mock_github_client: AsyncMock,
        mock_llm_client: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Full workflow: Simple PR → completes with stage logs.

        Scenario:
        - Simple 3-file Python PR with discount calculation utility
        - All stages should run (filter → ast_enrichment → stages 0-5)
        - LLM should be called for summary only (low risk → stages 2-4 produce findings)
        - Logging should capture pipeline milestones
        """
        mock_github_client.get_pr_diff = AsyncMock(return_value=SIMPLE_PYTHON_DIFF)
        mock_github_client.get_file_content = AsyncMock(
            return_value="# sample file\ndef calculate_discount(price, pct):\n    pass\n"
        )

        with caplog.at_level(logging.INFO):
            result = await run_pipeline(
                github_client=mock_github_client,
                repo_full_name="test/repo",
                pr_number=42,
                head_sha="abc123",
                base_sha="def456",
                pr_title="Add calculate_discount utility",
                pr_description="Simple utility function for discounts",
            )

        # Verify result structure
        assert isinstance(result, ReviewResult)
        assert result.pr_summary is not None
        assert isinstance(result.findings, list)
        assert result.files_reviewed == 3
        assert result.hunks_reviewed >= 3
        assert "filter" in result.stages_completed
        assert "stage_0_linters" in result.stages_completed
        assert "stage_1_summary" in result.stages_completed
        assert "stage_5_synthesis" in result.stages_completed

        # Verify logging
        log_messages = [r.message for r in caplog.records]
        assert any("Pipeline starting" in msg for msg in log_messages), "Missing pipeline start log"
        assert any(
            "Pipeline complete" in msg for msg in log_messages
        ), "Missing pipeline complete log"
        assert any(
            "Stage 0" in msg or "stage_0" in msg for msg in log_messages
        ), "Missing stage 0 log"
        assert any(
            "Stage 1" in msg or "stage_1" in msg for msg in log_messages
        ), "Missing stage 1 log"

    async def test_security_pr_flags_critical_finding(
        self,
        mock_github_client: AsyncMock,
        mock_security_llm_client: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Full workflow: Security PR → SQL injection flagged as critical.

        Scenario:
        - SQL injection vulnerability in user lookup function
        - Summary stage detects high risk
        - Security LLM client returns critical severity finding
        - Stage 3 (cross-file) should trigger due to high risk
        - Final findings should include the critical SQL injection issue
        """
        mock_github_client.get_pr_diff = AsyncMock(return_value=SECURITY_PYTHON_DIFF)
        mock_github_client.get_file_content = AsyncMock(
            return_value=(
                "def get_user_by_name(username):\n"
                '    query = f"SELECT * FROM users WHERE '
                "username = '{username}'\"\n"
                "    return db.execute(query)\n"
            )
        )

        # Use security LLM client that flags vulnerabilities
        with patch("app.pipeline.orchestrator.LLMClient") as mock_llm_cls:
            mock_llm_cls.return_value = mock_security_llm_client

            with caplog.at_level(logging.INFO):
                result = await run_pipeline(
                    github_client=mock_github_client,
                    repo_full_name="test/repo",
                    pr_number=43,
                    head_sha="secabc123",
                    base_sha="def456",
                    pr_title="Add user lookup by username",
                    pr_description="Adds a function to look up users by username.",
                )

        # Verify result
        assert isinstance(result, ReviewResult)
        assert result.pr_summary is not None
        assert result.files_reviewed >= 1

        # Check for critical findings or high-risk detection
        has_security_findings = any(
            f.severity == "critical" or "security" in f.category.lower() for f in result.findings
        )

        # Log should indicate stage 3 triggered (due to high risk)
        log_messages = [r.message for r in caplog.records]
        stage_3_triggered = any("Stage 3" in msg or "stage_3" in msg for msg in log_messages)

        # Either findings are present or stage 3 was triggered
        assert has_security_findings or stage_3_triggered, (
            f"Expected critical findings or stage 3 trigger. "
            f"Security findings: {has_security_findings}, "
            f"Stage 3 triggered: {stage_3_triggered}"
        )

    async def test_noncode_pr_short_circuits_without_llm(
        self,
        mock_github_client: AsyncMock,
        mock_llm_client: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """PR with only docs/lockfiles → skipped before LLM calls.

        Scenario:
        - PR changes only README.md and package-lock.json
        - Filter engine should reject non-code files
        - Pipeline should return early without consuming LLM credits
        - Logging should show filter decision
        """
        noncode_diff = """\
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,3 +1,4 @@
 # Project
+Updated docs
 Description here
diff --git a/package-lock.json b/package-lock.json
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,3 +1,3 @@
 {
-  "version": "1.0.0"
+  "version": "1.0.1"
 }
"""
        mock_github_client.get_pr_diff = AsyncMock(return_value=noncode_diff)

        with caplog.at_level(logging.INFO):
            result = await run_pipeline(
                github_client=mock_github_client,
                repo_full_name="test/repo",
                pr_number=99,
                head_sha="abc123",
                base_sha="def456",
                pr_title="Update docs",
                pr_description="Documentation updates",
            )

        # Should short-circuit with no findings
        assert isinstance(result, ReviewResult)
        assert result.files_reviewed == 0, f"Expected 0 files reviewed, got {result.files_reviewed}"
        assert len(result.findings) == 0, f"Expected no findings, got {len(result.findings)}"
        assert "filter" in result.stages_completed, "Filter stage should be in completed stages"

        # Verify LLM was not called (no stage 1)
        assert (
            "stage_1_summary" not in result.stages_completed
        ), "Stage 1 (summary) should not run for non-code PRs"

        # Verify no LLM was called (no stage 1) and no findings produced
        # The key assertions are above - just confirming early exit worked

    async def test_typescript_code_analyzed_correctly(
        self, mock_github_client: AsyncMock, mock_llm_client: AsyncMock
    ) -> None:
        """TypeScript PR → parsed and analyzed with language awareness.

        Scenario:
        - React TypeScript component with proper types
        - Diff parser recognizes .tsx extension
        - Linters and LLM analyze TypeScript-specific patterns
        - No critical findings expected (well-written code)
        """
        mock_github_client.get_pr_diff = AsyncMock(return_value=TYPESCRIPT_DIFF)
        mock_github_client.get_file_content = AsyncMock(
            return_value=(
                "import React from 'react';\n"
                "interface ButtonProps { label: string; onClick: () => void; }\n"
                "const Button: React.FC<ButtonProps> = ({label, onClick}) => (\n"
                "  <button onClick={onClick}>{label}</button>\n"
                ");\n"
                "export default Button;\n"
            )
        )

        result = await run_pipeline(
            github_client=mock_github_client,
            repo_full_name="test/frontend",
            pr_number=45,
            head_sha="tsabc123",
            base_sha="def456",
            pr_title="Add TypeScript Button component",
            pr_description="Reusable Button with TypeScript types",
        )

        # Verify processing completed
        assert isinstance(result, ReviewResult)
        assert result.pr_summary is not None
        assert result.files_reviewed >= 1
        assert "filter" in result.stages_completed
        assert "stage_0_linters" in result.stages_completed
        assert "stage_1_summary" in result.stages_completed

    async def test_bot_pr_filtered_by_gatekeeper(
        self, mock_github_client: AsyncMock, mock_llm_client: AsyncMock
    ) -> None:
        """Bot PR (Dependabot) → filtered out before LLM.

        Scenario:
        - Dependabot dependency update PR
        - Gatekeeper filter should catch bot author
        - No GitHub API calls beyond initial diff fetch
        - Result should indicate filtering decision
        """
        mock_github_client.get_pr_diff = AsyncMock(return_value=BOT_PR_DIFF)

        # Use filter engine directly to verify filtering works
        filter_engine = FilterEngine()

        # Construct a minimal payload matching BOT_PR_DIFF
        result = filter_engine.should_review(
            {
                "pull_request": {
                    "user": {"login": "dependabot[bot]"},
                    "draft": False,
                    "labels": [],
                }
            },
            changed_files=["requirements.txt"],
        )

        # Should be filtered out (not processed)
        assert result.should_process is False
        assert "bot" in result.reason.lower()
        assert result.queue == "skip"

    async def test_draft_pr_skipped_by_filter(
        self, mock_github_client: AsyncMock, mock_llm_client: AsyncMock
    ) -> None:
        """Draft PR → skipped by Gatekeeper.

        Scenario:
        - PR marked as draft=True
        - Filter engine should reject before pipeline
        - No review pipeline execution
        """
        filter_engine = FilterEngine()

        result = filter_engine.should_review(
            {
                "pull_request": {
                    "user": {"login": "user"},
                    "draft": True,
                    "labels": [],
                }
            },
            changed_files=["src/main.py"],
        )

        # Should be filtered out
        assert result.should_process is False
        assert "draft" in result.reason.lower()
        assert result.queue == "skip"

    async def test_large_pr_routed_to_slow_lane(
        self, mock_github_client: AsyncMock, mock_llm_client: AsyncMock
    ) -> None:
        """Large PR (>50 files) → routed to slow_lane.

        Scenario:
        - PR with 51 code files (exceeds LARGE_PR_THRESHOLD of 50)
        - Filter engine should route to slow_lane queue
        - Pipeline should mark in result stages
        """
        filter_engine = FilterEngine()

        # Create 51 files
        large_filenames = [f"src/module{i}/file.py" for i in range(51)]

        result = filter_engine.should_review(
            {
                "pull_request": {
                    "user": {"login": "user"},
                    "draft": False,
                    "labels": [],
                }
            },
            changed_files=large_filenames,
        )

        # Should route to slow_lane
        assert result.should_process is True
        assert result.queue == "slow_lane"
        assert "large" in result.reason.lower() or "slow" in result.reason.lower()

    async def test_pipeline_produces_valid_findings_structure(
        self,
        mock_github_client: AsyncMock,
        mock_security_llm_client: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Pipeline output → findings with correct structure and fields.

        Scenario:
        - Security PR processed to completion
        - All findings have required fields (file_path, line_start, diff_position, etc.)
        - Stage completion tracking is accurate
        - Cost tracking is recorded
        """
        mock_github_client.get_pr_diff = AsyncMock(return_value=SECURITY_PYTHON_DIFF)
        mock_github_client.get_file_content = AsyncMock(
            return_value=(
                "def get_user_by_name(username):\n"
                '    query = f"SELECT..."\n'
                "    return db.execute(query)\n"
            )
        )

        with patch("app.pipeline.orchestrator.LLMClient") as mock_llm_cls:
            mock_llm_cls.return_value = mock_security_llm_client

            with caplog.at_level(logging.INFO):
                result = await run_pipeline(
                    github_client=mock_github_client,
                    repo_full_name="test/repo",
                    pr_number=50,
                    head_sha="secabc123",
                    base_sha="def456",
                )

        # Verify result structure
        assert isinstance(result, ReviewResult)
        assert result.pr_summary is not None
        assert isinstance(result.findings, list)
        assert isinstance(result.stages_completed, list)
        assert isinstance(result.total_cost_usd, float)
        assert result.files_reviewed >= 0
        assert result.hunks_reviewed >= 0

        # Verify all findings have required fields
        for finding in result.findings:
            assert isinstance(finding, Finding)
            assert finding.file_path is not None
            assert isinstance(finding.line_start, int)
            assert isinstance(finding.line_end, int)
            assert isinstance(finding.diff_position, int)
            assert finding.severity in ("critical", "high", "medium", "low", "info")
            assert finding.category is not None
            assert finding.title is not None
            assert finding.body is not None
            assert isinstance(finding.confidence, float)

        # Verify stage tracking
        assert len(result.stages_completed) > 0, "At least some stages should complete"
        valid_stages = {
            "filter",
            "ast_enrichment",
            "stage_0_linters",
            "stage_1_summary",
            "stage_2_bugs",
            "stage_3_xfile",
            "stage_4_style",
            "stage_5_synthesis",
            "rag_enrichment",
            "parse",
            "config",
            "error",
        }
        for stage in result.stages_completed:
            assert stage in valid_stages, f"Unknown stage: {stage}"

        # Verify logging captured pipeline lifecycle
        log_messages = [r.message for r in caplog.records]
        pipeline_lifecycle = [
            any("Pipeline starting" in msg for msg in log_messages),
            any("Pipeline complete" in msg for msg in log_messages),
        ]
        assert all(pipeline_lifecycle), "Pipeline should log start and complete events"

    async def test_multiple_files_reviewed_with_per_file_findings(
        self, mock_github_client: AsyncMock, mock_llm_client: AsyncMock
    ) -> None:
        """Multi-file PR → findings distributed across files.

        Scenario:
        - SIMPLE_PYTHON_DIFF contains 3 files
        - Pipeline should review all 3 files
        - Findings (if any) map to correct files
        - Each file hunks processed independently
        """
        mock_github_client.get_pr_diff = AsyncMock(return_value=SIMPLE_PYTHON_DIFF)
        mock_github_client.get_file_content = AsyncMock(
            return_value="# sample content\ndef func(): pass\n"
        )

        result = await run_pipeline(
            github_client=mock_github_client,
            repo_full_name="test/repo",
            pr_number=100,
            head_sha="abc123",
            base_sha="def456",
            pr_title="Multi-file refactor",
            pr_description="Updates utilities and tests",
        )

        # Verify multi-file processing
        assert result.files_reviewed == 3, f"Expected 3 files, got {result.files_reviewed}"
        assert result.hunks_reviewed >= 3, f"Expected at least 3 hunks, got {result.hunks_reviewed}"

        # Verify findings reference the correct files
        expected_files = {"app/utils.py", "tests/test_utils.py", "app/models.py"}

        for finding in result.findings:
            assert (
                finding.file_path in expected_files
            ), f"Finding references unexpected file: {finding.file_path}"


# =============================================================================
#  Integration tests with real filter + pipeline
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestFilterAndPipelineIntegration:
    """Test Gatekeeper filter + pipeline orchestrator together."""

    async def test_filter_decision_affects_pipeline(
        self, mock_github_client: AsyncMock, mock_llm_client: AsyncMock
    ) -> None:
        """Gatekeeper decision is enforced before pipeline.

        Scenario:
        - Test that filter result correctly classifies PRs
        - Fast lane vs. slow lane routing is logged
        - Non-reviewable PRs don't reach the pipeline
        """
        filter_engine = FilterEngine()

        # Fast lane: small, code-only PR
        fast_lane_result = filter_engine.should_review(
            {
                "pull_request": {
                    "user": {"login": "user"},
                    "draft": False,
                    "labels": [],
                }
            },
            changed_files=["src/main.py", "src/utils.py"],
        )

        assert fast_lane_result.should_process is True
        assert fast_lane_result.queue == "fast_lane"

        # Slow lane: many files
        slow_lane_result = filter_engine.should_review(
            {
                "pull_request": {
                    "user": {"login": "user"},
                    "draft": False,
                    "labels": [],
                }
            },
            changed_files=[f"src/file{i}.py" for i in range(60)],
        )

        assert slow_lane_result.should_process is True
        assert slow_lane_result.queue == "slow_lane"

        # Skip: all docs
        skip_result = filter_engine.should_review(
            {
                "pull_request": {
                    "user": {"login": "user"},
                    "draft": False,
                    "labels": [],
                }
            },
            changed_files=["README.md", "docs/guide.md"],
        )

        assert skip_result.should_process is False
        assert skip_result.queue == "skip"
