"""Celery task: full repository indexing.

Implements ADR-0030 (GitHub tree API indexing).
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
    name="openrabbit.index_repository",
    max_retries=2,
    default_retry_delay=60,
    acks_late=True,
)
def index_repository_task(
    self: Any,
    installation_id: int,
    repo_full_name: str,
    repo_id: int,
) -> dict[str, Any]:
    """Index all code files in a repository.

    Sync Celery task that bridges to the async indexer.

    Args:
        installation_id: GitHub App installation ID.
        repo_full_name: ``owner/repo`` format.
        repo_id: Database repository ID (Qdrant namespace).

    Returns:
        Dict with indexing status and chunk count.
    """
    start_time = time.monotonic()

    logger.info(
        "Index task started",
        extra={
            "task_id": self.request.id,
            "installation_id": installation_id,
            "repo": repo_full_name,
            "repo_id": repo_id,
        },
    )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                asyncio.run,
                _run_async_indexing(
                    installation_id=installation_id,
                    repo_full_name=repo_full_name,
                    repo_id=repo_id,
                ),
            )
            result = future.result(timeout=1800)  # 30-minute hard cap for large repos

        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.info(
            "Index task completed",
            extra={
                "task_id": self.request.id,
                "repo": repo_full_name,
                "chunks_indexed": result.get("chunks_indexed", 0),
                "duration_ms": duration_ms,
            },
        )
        return result

    except Exception as exc:
        logger.exception(
            "Index task failed",
            extra={"task_id": self.request.id, "repo": repo_full_name},
        )
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
#  Async indexing bridge
# ---------------------------------------------------------------------------


async def _run_async_indexing(
    installation_id: int,
    repo_full_name: str,
    repo_id: int,
) -> dict[str, Any]:
    """Bridge from sync Celery task to async repository indexer."""
    import redis.asyncio as aioredis

    from app.config import get_settings
    from app.core.github_client import GitHubClient
    from app.rag.embedder import EmbeddingService
    from app.rag.indexer import RepositoryIndexer

    settings = get_settings()
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

    github: GitHubClient | None = None
    embedding_service: EmbeddingService | None = None

    try:
        github = GitHubClient(installation_id, redis_client)
        embedding_service = EmbeddingService(
            redis=redis_client,
            qdrant_url=settings.qdrant_url,
            openai_api_key=settings.openai_api_key,
        )

        # Ensure both Qdrant collections exist
        await embedding_service.ensure_collection()
        await embedding_service.ensure_collection(collection_name="past_findings")

        # Resolve default branch HEAD SHA
        head_sha = await _get_default_branch_sha(github, repo_full_name)

        indexer = RepositoryIndexer(
            embedding_service=embedding_service,
            github_client=github,
            redis=redis_client,
        )

        progress = await indexer.index_repository(repo_full_name, repo_id, head_sha)

        # Post a completion comment on the most recently updated open PR
        if progress.status == "completed":
            await _post_indexing_complete_comment(
                github, repo_full_name, progress.chunks_total
            )

        return {
            "status": progress.status,
            "chunks_indexed": progress.chunks_total,
            "files_indexed": progress.done,
            "error": progress.error or None,
        }

    finally:
        if embedding_service is not None:
            await embedding_service.aclose()
        if github is not None:
            await github.aclose()
        await redis_client.close()


async def _get_default_branch_sha(github: Any, repo_full_name: str) -> str:
    """Resolve the default branch HEAD commit SHA via the GitHub API.

    Uses:
      GET /repos/{owner}/{repo}           → default_branch name
      GET /repos/{owner}/{repo}/git/refs/heads/{branch} → sha
    """
    owner, repo = repo_full_name.split("/", 1)

    # Get default branch name
    repo_response = await github._request("GET", f"/repos/{owner}/{repo}")
    repo_data = repo_response.json()
    default_branch = repo_data.get("default_branch", "main")

    # Get HEAD SHA for that branch
    ref_response = await github._request(
        "GET", f"/repos/{owner}/{repo}/git/refs/heads/{default_branch}"
    )
    ref_data = ref_response.json()
    sha: str = ref_data["object"]["sha"]
    return sha


async def _post_indexing_complete_comment(
    github: Any,
    repo_full_name: str,
    chunks_total: int,
) -> None:
    """Post an indexing-complete message on the most recently updated open PR."""
    owner, repo = repo_full_name.split("/", 1)

    try:
        prs_response = await github._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls?state=open&sort=updated&per_page=1",
        )
        prs = prs_response.json()
        if not prs:
            return

        pr_number = prs[0]["number"]
        body = (
            "## OpenRabbit — Repository Indexed\n\n"
            f"The codebase has been indexed ({chunks_total:,} code chunks). "
            "Future PR reviews will now include **codebase-aware** analysis:\n\n"
            "- Semantically related code retrieved from the full repository\n"
            "- Call sites of changed functions identified automatically\n"
            "- Similar past findings shown as few-shot examples\n\n"
            "_This message was posted automatically after initial indexing._"
        )
        await github.post_review_comment(
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            body=body,
        )
    except Exception:
        logger.debug(
            "Could not post indexing-complete comment for %s — skipping", repo_full_name
        )
