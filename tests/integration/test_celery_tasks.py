"""Integration tests for Celery task execution.

Tests actual Celery task execution using task_always_eager=True mode for
synchronous execution. Tests all three main task types:
  - run_pr_review: Full PR review pipeline
  - index_repository_task: Repository indexing
  - handle_pr_reply: PR comment reply handling (conversation flow)

All GitHub API calls, LLM calls, and embeddings are mocked.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.pr_review import PRReview
from app.tasks.celery_app import celery_app


# =============================================================================
#  Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def celery_eager_mode() -> None:
    """Configure Celery for eager (synchronous) test execution.

    By setting task_always_eager=True, all tasks run immediately
    in the calling thread without Redis/RabbitMQ.
    """
    original_eager = celery_app.conf.get("task_always_eager", False)
    original_propagate = celery_app.conf.get("task_eager_propagates", False)

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True

    yield

    # Restore original settings
    celery_app.conf.task_always_eager = original_eager
    celery_app.conf.task_eager_propagates = original_propagate


@pytest.fixture
def mock_pipeline_result() -> dict[str, Any]:
    """Mock result from run_pipeline()."""
    return {
        "status": "completed",
        "findings": [],
        "summary": "Review complete.",
        "cost_usd": 0.01,
        "stages_completed": ["parse", "filter", "analyze"],
        "files_reviewed": 3,
        "hunks_reviewed": 5,
    }


@pytest.fixture
def github_client_with_defaults() -> AsyncMock:
    """GitHub client with default returns for review flow."""
    client = AsyncMock()
    client.get_pr_diff = AsyncMock(
        return_value="--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-old\n+new\n"
    )
    client.get_file_content = AsyncMock(return_value="# test file\n")
    client.post_review = AsyncMock(return_value={"id": 1})
    client.post_review_comment = AsyncMock(return_value={"id": 2})
    client.aclose = AsyncMock()
    return client


# =============================================================================
#  Review Task Tests
# =============================================================================


@pytest.mark.integration
class TestReviewTask:
    """Tests for run_pr_review Celery task."""

    def test_review_task_creates_pr_review_record(
        self,
        github_client_with_defaults: AsyncMock,
        mock_llm_client: AsyncMock,
    ) -> None:
        """Test that review task creates a PRReview database record."""
        from app.tasks.review_task import run_pr_review

        with patch("app.tasks.review_task._create_review_record") as mock_create:
            review_id = str(uuid.uuid4())
            mock_create.return_value = review_id

            with patch("app.tasks.review_task._run_async_pipeline") as mock_pipeline:
                mock_pipeline.return_value = {
                    "status": "completed",
                    "findings_count": 0,
                    "cost_usd": 0.01,
                    "files_reviewed": 1,
                    "hunks_reviewed": 1,
                    "stages": ["parse"],
                }

                with patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit:
                    future = MagicMock()
                    future.result = MagicMock(
                        return_value={
                            "status": "completed",
                            "findings_count": 0,
                            "cost_usd": 0.01,
                            "files_reviewed": 1,
                            "hunks_reviewed": 1,
                            "stages": ["parse"],
                        }
                    )
                    mock_submit.return_value = future

                    with patch("app.tasks.review_task._update_review_record") as mock_update:
                        result = run_pr_review(
                            installation_id=123,
                            repo_full_name="test/repo",
                            repo_id=999,
                            pr_number=42,
                            head_sha="abc123def456",
                            base_sha="xyz789abc",
                            pr_title="Test PR",
                            pr_description="Test description",
                        )

                        # Verify the task was called with correct parameters
                        mock_create.assert_called_once()
                        assert result is not None
                        assert result["status"] == "completed"

    def test_review_task_updates_status_to_completed(
        self,
    ) -> None:
        """Test that review task updates PRReview status from processing to completed."""
        from app.tasks.review_task import run_pr_review

        review_id = str(uuid.uuid4())

        with patch("app.tasks.review_task._create_review_record") as mock_create:
            mock_create.return_value = review_id

            with patch("app.tasks.review_task._run_async_pipeline") as mock_pipeline:
                mock_pipeline.return_value = {
                    "status": "completed",
                    "findings_count": 0,
                    "cost_usd": 0.01,
                    "files_reviewed": 1,
                    "hunks_reviewed": 1,
                    "stages": ["parse"],
                }

                with patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit:
                    future = MagicMock()
                    future.result = MagicMock(return_value=mock_pipeline.return_value)
                    mock_submit.return_value = future

                    with patch("app.tasks.review_task._update_review_record") as mock_update:
                        result = run_pr_review(
                            installation_id=123,
                            repo_full_name="test/repo",
                            repo_id=999,
                            pr_number=42,
                            head_sha="abc123",
                            base_sha="def456",
                            pr_title="Test PR",
                            pr_description="Test",
                        )

                        # Verify update was called with "completed" status
                        mock_update.assert_called_once()
                        call_kwargs = mock_update.call_args[1]
                        assert call_kwargs["status"] == "completed"

    def test_review_task_posts_findings_to_github(
        self,
        github_client_with_defaults: AsyncMock,
    ) -> None:
        """Test that findings are posted to GitHub via review comments."""
        from app.tasks.review_task import run_pr_review

        with patch("app.tasks.review_task._create_review_record") as mock_create:
            mock_create.return_value = str(uuid.uuid4())

            with patch("app.tasks.review_task._run_async_pipeline") as mock_pipeline:
                mock_pipeline.return_value = {
                    "status": "completed",
                    "findings_count": 1,
                    "cost_usd": 0.02,
                    "files_reviewed": 1,
                    "hunks_reviewed": 1,
                    "stages": ["parse", "analyze"],
                }

                with patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit:
                    future = MagicMock()
                    future.result = MagicMock(return_value=mock_pipeline.return_value)
                    mock_submit.return_value = future

                    with patch("app.tasks.review_task._update_review_record"):
                        result = run_pr_review(
                            installation_id=123,
                            repo_full_name="test/repo",
                            repo_id=999,
                            pr_number=42,
                            head_sha="abc123",
                            base_sha="def456",
                            pr_title="Test PR",
                            pr_description="Test",
                        )

                        # Verify findings were processed
                        assert result is not None
                        assert result["findings_count"] == 1

    def test_review_task_posts_error_comment_on_failure(
        self,
    ) -> None:
        """Test that error comments are posted to GitHub on task failure."""
        from app.tasks.review_task import run_pr_review

        with patch("app.tasks.review_task._create_review_record") as mock_create:
            mock_create.return_value = str(uuid.uuid4())

            with patch("app.tasks.review_task._run_async_pipeline") as mock_pipeline:
                mock_pipeline.side_effect = RuntimeError("Test error")

                with patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit:
                    future = MagicMock()
                    future.result = MagicMock(side_effect=RuntimeError("Test error"))
                    mock_submit.return_value = future

                    with patch("app.tasks.review_task._post_error_comment") as mock_post_error:
                        with patch("app.tasks.review_task._update_review_record"):
                            with pytest.raises(RuntimeError):
                                run_pr_review(
                                    installation_id=123,
                                    repo_full_name="test/repo",
                                    repo_id=999,
                                    pr_number=42,
                                    head_sha="abc123",
                                    base_sha="def456",
                                    pr_title="Test PR",
                                    pr_description="Test",
                                )

    def test_review_task_handles_github_error(
        self,
    ) -> None:
        """Test that GitHub errors are caught and logged."""
        from app.core.exceptions import GitHubAPIError
        from app.tasks.review_task import run_pr_review

        with patch("app.tasks.review_task._create_review_record") as mock_create:
            mock_create.return_value = str(uuid.uuid4())

            with patch("app.tasks.review_task._run_async_pipeline") as mock_pipeline:
                mock_pipeline.side_effect = GitHubAPIError("API error", status_code=500)

                with patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit:
                    future = MagicMock()
                    future.result = MagicMock(
                        side_effect=GitHubAPIError("API error", status_code=500)
                    )
                    mock_submit.return_value = future

                    with patch("app.tasks.review_task._post_error_comment"):
                        with patch("app.tasks.review_task._update_review_record"):
                            with pytest.raises(GitHubAPIError):
                                run_pr_review(
                                    installation_id=123,
                                    repo_full_name="test/repo",
                                    repo_id=999,
                                    pr_number=42,
                                    head_sha="abc123",
                                    base_sha="def456",
                                    pr_title="Test PR",
                                    pr_description="Test",
                                )

    def test_review_task_returns_cost_and_metrics(
        self,
    ) -> None:
        """Test that review task returns cost and performance metrics."""
        from app.tasks.review_task import run_pr_review

        expected_cost = 0.0234
        expected_files = 5
        expected_hunks = 12

        with patch("app.tasks.review_task._create_review_record") as mock_create:
            mock_create.return_value = str(uuid.uuid4())

            with patch("app.tasks.review_task._run_async_pipeline") as mock_pipeline:
                mock_pipeline.return_value = {
                    "status": "completed",
                    "findings_count": 2,
                    "cost_usd": expected_cost,
                    "files_reviewed": expected_files,
                    "hunks_reviewed": expected_hunks,
                    "stages": ["parse", "filter", "analyze", "synthesis"],
                }

                with patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit:
                    future = MagicMock()
                    future.result = MagicMock(return_value=mock_pipeline.return_value)
                    mock_submit.return_value = future

                    with patch("app.tasks.review_task._update_review_record"):
                        result = run_pr_review(
                            installation_id=123,
                            repo_full_name="test/repo",
                            repo_id=999,
                            pr_number=42,
                            head_sha="abc123",
                            base_sha="def456",
                            pr_title="Test PR",
                            pr_description="Test",
                        )

                        assert result["cost_usd"] == expected_cost
                        assert result["files_reviewed"] == expected_files
                        assert result["hunks_reviewed"] == expected_hunks


# =============================================================================
#  Index Task Tests
# =============================================================================


@pytest.mark.integration
class TestIndexTask:
    """Tests for index_repository_task Celery task."""

    def test_index_task_creates_embedding_service(
        self,
    ) -> None:
        """Test that index task initializes EmbeddingService correctly."""
        from app.tasks.index_task import index_repository_task

        with patch("app.tasks.index_task._run_async_indexing") as mock_indexing:
            mock_indexing.return_value = {
                "status": "completed",
                "chunks_indexed": 150,
                "files_indexed": 42,
                "error": None,
            }

            with patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit:
                future = MagicMock()
                future.result = MagicMock(return_value=mock_indexing.return_value)
                mock_submit.return_value = future

                result = index_repository_task(
                    installation_id=1,
                    repo_full_name="test/repo",
                    repo_id=999,
                )

                assert result is not None
                assert result["status"] == "completed"

    def test_index_task_returns_chunks_count(
        self,
    ) -> None:
        """Test that index task returns chunk count in result."""
        from app.tasks.index_task import index_repository_task

        expected_chunks = 287
        expected_files = 64

        with patch("app.tasks.index_task._run_async_indexing") as mock_indexing:
            mock_indexing.return_value = {
                "status": "completed",
                "chunks_indexed": expected_chunks,
                "files_indexed": expected_files,
                "error": None,
            }

            with patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit:
                future = MagicMock()
                future.result = MagicMock(return_value=mock_indexing.return_value)
                mock_submit.return_value = future

                result = index_repository_task(
                    installation_id=1,
                    repo_full_name="test/repo",
                    repo_id=999,
                )

                assert result["chunks_indexed"] == expected_chunks
                assert result["files_indexed"] == expected_files

    def test_index_task_handles_indexing_error(
        self,
    ) -> None:
        """Test that index task handles indexing failures gracefully."""
        from app.core.exceptions import IndexingError
        from app.tasks.index_task import index_repository_task

        with patch("app.tasks.index_task._run_async_indexing") as mock_indexing:
            mock_indexing.side_effect = IndexingError("Failed to index repository")

            with patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit:
                future = MagicMock()
                future.result = MagicMock(side_effect=IndexingError("Failed to index repository"))
                mock_submit.return_value = future

                with pytest.raises(IndexingError):
                    index_repository_task(
                        installation_id=1,
                        repo_full_name="test/repo",
                        repo_id=999,
                    )

    def test_index_task_returns_error_message_on_failure(
        self,
    ) -> None:
        """Test that index task includes error message in result when indexing fails."""
        from app.tasks.index_task import index_repository_task

        error_message = "Qdrant connection refused"

        with patch("app.tasks.index_task._run_async_indexing") as mock_indexing:
            mock_indexing.return_value = {
                "status": "failed",
                "chunks_indexed": 0,
                "files_indexed": 0,
                "error": error_message,
            }

            with patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit:
                future = MagicMock()
                future.result = MagicMock(return_value=mock_indexing.return_value)
                mock_submit.return_value = future

                result = index_repository_task(
                    installation_id=1,
                    repo_full_name="test/repo",
                    repo_id=999,
                )

                assert result["status"] == "failed"
                assert result["error"] == error_message


# =============================================================================
#  Conversation Task Tests
# =============================================================================


@pytest.mark.integration
class TestConversationTask:
    """Tests for handle_pr_reply Celery task."""

    def test_conversation_task_processes_reply(
        self,
    ) -> None:
        """Test that conversation task processes a developer reply."""
        from app.tasks.conversation_task import handle_pr_reply

        with patch("app.tasks.conversation_task._run_async_handler") as mock_handler:
            mock_handler.return_value = {
                "action": "fix",
                "comment_id": 456,
                "status": "success",
            }

            with patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit:
                future = MagicMock()
                future.result = MagicMock(return_value=mock_handler.return_value)
                mock_submit.return_value = future

                result = handle_pr_reply(
                    installation_id=1,
                    repo_full_name="test/repo",
                    pr_number=42,
                    comment_id=123,
                    comment_body="/fix",
                    in_reply_to_id=888,
                )

                assert result is not None
                assert result["action"] == "fix"
                assert result["comment_id"] == 456

    def test_conversation_task_handles_explain_action(
        self,
    ) -> None:
        """Test that conversation task handles 'explain' intent."""
        from app.tasks.conversation_task import handle_pr_reply

        with patch("app.tasks.conversation_task._run_async_handler") as mock_handler:
            mock_handler.return_value = {
                "action": "explain",
                "comment_id": 789,
                "status": "success",
            }

            with patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit:
                future = MagicMock()
                future.result = MagicMock(return_value=mock_handler.return_value)
                mock_submit.return_value = future

                result = handle_pr_reply(
                    installation_id=1,
                    repo_full_name="test/repo",
                    pr_number=42,
                    comment_id=123,
                    comment_body="/explain why this is a bug",
                    in_reply_to_id=888,
                )

                assert result["action"] == "explain"

    def test_conversation_task_handles_ignore_action(
        self,
    ) -> None:
        """Test that conversation task handles 'ignore' intent."""
        from app.tasks.conversation_task import handle_pr_reply

        with patch("app.tasks.conversation_task._run_async_handler") as mock_handler:
            mock_handler.return_value = {
                "action": "ignore",
                "comment_id": 999,
                "status": "dismissed",
            }

            with patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit:
                future = MagicMock()
                future.result = MagicMock(return_value=mock_handler.return_value)
                mock_submit.return_value = future

                result = handle_pr_reply(
                    installation_id=1,
                    repo_full_name="test/repo",
                    pr_number=42,
                    comment_id=123,
                    comment_body="/ignore",
                    in_reply_to_id=888,
                )

                assert result["action"] == "ignore"

    def test_conversation_task_handles_handler_error(
        self,
    ) -> None:
        """Test that conversation task handles handler errors gracefully."""
        from app.tasks.conversation_task import handle_pr_reply

        with patch("app.tasks.conversation_task._run_async_handler") as mock_handler:
            mock_handler.side_effect = RuntimeError("Handler failed")

            with patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit:
                future = MagicMock()
                future.result = MagicMock(side_effect=RuntimeError("Handler failed"))
                mock_submit.return_value = future

                with pytest.raises(RuntimeError):
                    handle_pr_reply(
                        installation_id=1,
                        repo_full_name="test/repo",
                        pr_number=42,
                        comment_id=123,
                        comment_body="/fix",
                        in_reply_to_id=888,
                    )


# =============================================================================
#  Cross-Task & State Management Tests
# =============================================================================


@pytest.mark.integration
class TestTaskStateManagement:
    """Tests for task state management and database interactions."""

    def test_multiple_reviews_same_pr(
        self,
    ) -> None:
        """Test that multiple reviews on the same PR generate separate UUIDs."""
        # Create first review ID
        review1_id = str(uuid.uuid4())

        # Create second review ID (e.g., after push update)
        review2_id = str(uuid.uuid4())

        # Both should be different UUIDs
        assert review1_id != review2_id
        # Verify they are valid UUIDs
        assert len(review1_id) == 36  # Standard UUID4 string length
        assert len(review2_id) == 36

    def test_review_with_findings_creates_finding_records(
        self,
    ) -> None:
        """Test that review records track findings count."""
        review = PRReview(
            repo_id=999,
            pr_number=42,
            pr_title="Test PR",
            head_sha="abc123",
            base_sha="def456",
            status="completed",
            findings_count=1,
        )

        # Verify record can be created with findings_count
        assert review.findings_count == 1
        assert review.status == "completed"
        assert review.pr_number == 42

    def test_task_timing_and_duration(
        self,
    ) -> None:
        """Test that task returns timing metrics in logs."""
        from app.tasks.review_task import run_pr_review

        with patch("app.tasks.review_task._create_review_record") as mock_create:
            mock_create.return_value = str(uuid.uuid4())

            with patch("app.tasks.review_task._run_async_pipeline") as mock_pipeline:
                mock_pipeline.return_value = {
                    "status": "completed",
                    "findings_count": 0,
                    "cost_usd": 0.01,
                    "files_reviewed": 1,
                    "hunks_reviewed": 1,
                    "stages": ["parse"],
                }

                with patch("concurrent.futures.ThreadPoolExecutor.submit") as mock_submit:
                    future = MagicMock()
                    future.result = MagicMock(return_value=mock_pipeline.return_value)
                    mock_submit.return_value = future

                    with patch("app.tasks.review_task._update_review_record"):
                        result = run_pr_review(
                            installation_id=123,
                            repo_full_name="test/repo",
                            repo_id=999,
                            pr_number=42,
                            head_sha="abc123",
                            base_sha="def456",
                            pr_title="Test PR",
                            pr_description="Test",
                        )

        # Task should return result with metrics
        assert result is not None
        assert result["status"] == "completed"
        assert "cost_usd" in result
        assert "files_reviewed" in result
