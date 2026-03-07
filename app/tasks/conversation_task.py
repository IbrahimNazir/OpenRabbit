"""Celery task: handle PR review comment replies ("Fix this" flow).

Implements ADR-0037 (conversation reply flow).
Follows the same sync→async bridge pattern as review_task.py.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import time
from typing import Any

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="openrabbit.handle_pr_reply",
    max_retries=2,
    default_retry_delay=15,
    acks_late=True,
    queue="fast_lane",
)
def handle_pr_reply(
    self: Any,
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    comment_id: int,
    comment_body: str,
    in_reply_to_id: int | None,
) -> dict[str, Any]:
    """Handle a developer reply to an AI review comment.

    Dispatches intent detection and action (fix / explain / ignore)
    to the ConversationHandler running inside asyncio.run().

    Args:
        installation_id: GitHub App installation ID.
        repo_full_name: ``owner/repo`` format.
        pr_number: Pull request number.
        comment_id: The reply comment's own GitHub ID.
        comment_body: Raw body text of the reply.
        in_reply_to_id: GitHub ID of the comment being replied to (may be None).

    Returns:
        Dict with action taken and resulting comment ID.
    """
    start_time = time.monotonic()

    logger.info(
        "Conversation task started",
        extra={
            "task_id": self.request.id,
            "installation_id": installation_id,
            "repo": repo_full_name,
            "pr_number": pr_number,
            "comment_id": comment_id,
            "in_reply_to_id": in_reply_to_id,
        },
    )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                asyncio.run,
                _run_async_handler(
                    installation_id=installation_id,
                    repo_full_name=repo_full_name,
                    pr_number=pr_number,
                    comment_id=comment_id,
                    comment_body=comment_body,
                    in_reply_to_id=in_reply_to_id,
                ),
            )
            result = future.result(timeout=120)  # 2-minute hard cap

        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.info(
            "Conversation task completed",
            extra={
                "task_id": self.request.id,
                "repo": repo_full_name,
                "pr_number": pr_number,
                "action": result.get("action"),
                "duration_ms": duration_ms,
            },
        )
        return result

    except Exception as exc:
        logger.exception(
            "Conversation task failed",
            extra={
                "task_id": self.request.id,
                "repo": repo_full_name,
                "pr_number": pr_number,
            },
        )
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
#  Async handler bridge
# ---------------------------------------------------------------------------


async def _run_async_handler(
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    comment_id: int,
    comment_body: str,
    in_reply_to_id: int | None,
) -> dict[str, Any]:
    """Bridge from sync Celery task to async ConversationHandler."""
    import redis.asyncio as aioredis

    from app.config import get_settings
    from app.conversation.handler import ConversationHandler
    from app.core.github_client import GitHubClient

    settings = get_settings()
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

    try:
        github = GitHubClient(installation_id, redis_client)
        handler = ConversationHandler(github_client=github)

        return await handler.handle(
            installation_id=installation_id,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            comment_id=comment_id,
            comment_body=comment_body,
            in_reply_to_id=in_reply_to_id,
            redis_client=redis_client,
        )
    finally:
        await redis_client.close()
