"""Celery task: run a full PR review.

Implements ADR-0013: sync Celery task that bridges to the async pipeline.
Creates a PRReview DB record, runs the pipeline, posts results to GitHub,
and updates the DB with final status.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.tasks.celery_app import celery_app
from app.core.exceptions import GitHubError, LLMError

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="openrabbit.review_pr",
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def run_pr_review(
    self: Any,
    installation_id: int,
    repo_full_name: str,
    repo_id: int,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    pr_title: str = "",
    pr_description: str = "",
) -> dict[str, Any]:
    """Execute a full PR review pipeline as a Celery task.

    This is a sync task that runs the async pipeline via asyncio.run().
    Uses the sync DB engine for record keeping (per ADR-0006).

    Args:
        installation_id: GitHub App installation ID.
        repo_full_name: ``owner/repo`` format.
        repo_id: GitHub repository ID.
        pr_number: Pull request number.
        head_sha: HEAD commit SHA.
        base_sha: BASE commit SHA.
        pr_title: PR title for summarization.
        pr_description: PR body for summarization.

    Returns:
        Dict with review status, findings count, and cost.
    """
    start_time = time.monotonic()
    review_id: str | None = None

    logger.info(
        "Review task started",
        extra={
            "task_id": self.request.id,
            "installation_id": installation_id,
            "repo": repo_full_name,
            "pr_number": pr_number,
            "head_sha": head_sha[:8],
        },
    )

    try:
        # Create PRReview record in DB
        review_id = _create_review_record(
            repo_id=repo_id,
            pr_number=pr_number,
            pr_title=pr_title,
            head_sha=head_sha,
            base_sha=base_sha,
        )

        # Run the async pipeline in a safe event loop for Celery
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        result = loop.run_until_complete(
            _run_async_pipeline(
                installation_id=installation_id,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                head_sha=head_sha,
                base_sha=base_sha,
                pr_title=pr_title,
                pr_description=pr_description,
            )
        )

        # Update DB record with results
        duration_ms = int((time.monotonic() - start_time) * 1000)
        _update_review_record(
            review_id=review_id,
            status="completed",
            findings_count=len(result.get("findings", [])),
            cost_usd=result.get("cost_usd", 0.0),
        )

        logger.info(
            "Review task completed",
            extra={
                "task_id": self.request.id,
                "repo": repo_full_name,
                "pr_number": pr_number,
                "findings": result.get("findings_count", 0),
                "cost_usd": result.get("cost_usd", 0),
                "duration_ms": duration_ms,
            },
        )

        return result

    except (GitHubError, LLMError) as exc:
        logger.exception(
            "Review task failed with known error",
            extra={
                "task_id": self.request.id,
                "repo": repo_full_name,
                "pr_number": pr_number,
                "error_type": type(exc).__name__,
            },
        )
        if review_id:
            _update_review_record(review_id, status="failed", error_message=str(exc))

        # Post error comment on the PR
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            loop.run_until_complete(
                _post_error_comment(installation_id, repo_full_name, pr_number)
            )
        except Exception:
            logger.exception("Failed to post error comment")

        # Retry on transient errors
        raise self.retry(exc=exc)

    except Exception as exc:
        logger.exception(
            "Review task failed with unexpected error",
            extra={"task_id": self.request.id, "repo": repo_full_name},
        )
        if review_id:
            _update_review_record(review_id, status="failed", error_message=str(exc))

        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            loop.run_until_complete(
                _post_error_comment(installation_id, repo_full_name, pr_number)
            )
        except Exception:
            logger.exception("Failed to post error comment")

        raise


# ---------------------------------------------------------------------------
#  Async pipeline bridge
# ---------------------------------------------------------------------------


async def _run_async_pipeline(
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    pr_title: str,
    pr_description: str,
) -> dict[str, Any]:
    """Bridge from sync Celery task to async pipeline."""
    import redis.asyncio as aioredis

    from app.config import get_settings
    from app.core.comment_formatter import format_finding_comment, format_summary_comment
    from app.core.github_client import GitHubClient
    from app.pipeline.orchestrator import run_pipeline

    settings = get_settings()

    # Create async Redis connection for the GitHub client
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

    try:
        github = GitHubClient(installation_id, redis_client)

        # Run the pipeline
        review_result = await run_pipeline(
            github_client=github,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            head_sha=head_sha,
            base_sha=base_sha,
            pr_title=pr_title,
            pr_description=pr_description,
        )

        # Post inline review comments to GitHub
        if review_result.findings:
            comments: list[dict[str, Any]] = []
            for finding in review_result.findings:
                comment_body = format_finding_comment(finding)
                comments.append({
                    "path": finding.file_path,
                    "position": finding.diff_position,
                    "body": comment_body,
                })

            # Post the review with all inline comments
            try:
                await github.post_review(
                    repo_full_name=repo_full_name,
                    pr_number=pr_number,
                    head_sha=head_sha,
                    comments=comments,
                    body="",  # Summary goes as a separate comment
                )
            except Exception:
                logger.warning("Batch review post failed — trying individual comments")
                # Fallback: post comments individually, skipping bad ones
                for comment in comments:
                    try:
                        await github.post_review(
                            repo_full_name=repo_full_name,
                            pr_number=pr_number,
                            head_sha=head_sha,
                            comments=[comment],
                            body="",
                        )
                    except Exception:
                        logger.warning(
                            "Skipping comment on %s — invalid position",
                            comment["path"],
                        )

        # Post summary comment
        summary_body = format_summary_comment(review_result)
        try:
            await github.post_review_comment(
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                body=summary_body,
            )
        except Exception:
            logger.exception("Failed to post summary comment")

        return {
            "status": "completed",
            "findings_count": len(review_result.findings),
            "cost_usd": review_result.total_cost_usd,
            "files_reviewed": review_result.files_reviewed,
            "hunks_reviewed": review_result.hunks_reviewed,
            "stages": review_result.stages_completed,
        }

    finally:
        await github.aclose()
        await redis_client.close()


async def _post_error_comment(
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
) -> None:
    """Post a user-friendly error comment on the PR."""
    import redis.asyncio as aioredis

    from app.config import get_settings
    from app.core.github_client import GitHubClient

    settings = get_settings()
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

    try:
        github = GitHubClient(installation_id, redis_client)
        await github.post_review_comment(
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            body=(
                "## 🐇 OpenRabbit\n\n"
                "⚠️ An error occurred while reviewing this PR. "
                "The team has been notified. Please try again later or "
                "open an issue if the problem persists."
            ),
        )
    finally:
        await github.aclose()
        await redis_client.close()


# ---------------------------------------------------------------------------
#  Database helpers (sync — runs in Celery worker)
# ---------------------------------------------------------------------------


def _create_review_record(
    repo_id: int,
    pr_number: int,
    pr_title: str,
    head_sha: str,
    base_sha: str,
) -> str:
    """Create a PRReview record and return its UUID as a string."""
    from app.models.database import get_sync_db
    from app.models.pr_review import PRReview

    session = get_sync_db()
    try:
        review = PRReview(
            repo_id=repo_id,
            pr_number=pr_number,
            pr_title=pr_title,
            head_sha=head_sha,
            base_sha=base_sha,
            status="processing",
            stage="pipeline",
        )
        session.add(review)
        session.commit()
        review_id = str(review.id)
        logger.info("Created PRReview record", extra={"review_id": review_id})
        return review_id
    except Exception:
        session.rollback()
        logger.exception("Failed to create PRReview record")
        raise
    finally:
        session.close()


def _update_review_record(
    review_id: str,
    *,
    status: str,
    findings_count: int = 0,
    cost_usd: float = 0.0,
    error_message: str = "",
) -> None:
    """Update a PRReview record with final status."""
    import uuid
    from datetime import datetime, timezone

    from app.models.database import get_sync_db
    from app.models.pr_review import PRReview

    session = get_sync_db()
    try:
        review = session.get(PRReview, uuid.UUID(review_id))
        if review:
            review.status = status
            review.findings_count = findings_count
            review.cost_usd = cost_usd
            review.error_message = error_message or None
            if status == "completed":
                review.completed_at = datetime.now(timezone.utc)
            session.commit()
    except Exception:
        session.rollback()
        logger.exception("Failed to update PRReview record")
    finally:
        session.close()
