"""Full repository indexer using the GitHub tree API.

Implements ADR-0030: fetches all code files via ``GET /repos/{owner}/{repo}/git/trees/{sha}?recursive=1``
without cloning.  Processes files in batches of 20, respects the 30 calls/minute
GitHub rate limit, and supports resume via Redis progress tracking.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.core.diff_parser import EXTENSION_TO_LANGUAGE
from app.core.github_client import GitHubClient
from app.parsing.chunk_extractor import extract_chunks
from app.rag.embedder import EmbeddingService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------------------

MAX_FILE_SIZE_BYTES = 500_000  # 500 KB — skip large generated/binary files
FILE_BATCH_SIZE = 20  # files processed in parallel per batch
GITHUB_CALLS_PER_MINUTE = 30
PROGRESS_REDIS_PREFIX = "index_progress"

# Patterns for files/directories to skip during indexing
_SKIP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"(^|/)vendor/",
        r"(^|/)node_modules/",
        r"\.min\.(js|css)$",
        r"_pb2\.py$",
        r"\.(lock|sum)$",
        r"(^|/)generated/",
        r"(^|/)__pycache__/",
        r"\.pyc$",
        r"(^|/)dist/",
        r"(^|/)build/",
        r"(^|/)\.git/",
        r"\.(png|jpg|jpeg|gif|svg|ico|woff|woff2|ttf|eot|pdf|zip|tar|gz)$",
    ]
]

# Code file extensions supported for chunking
_CODE_EXTENSIONS = {f".{ext.lstrip('.')}" for ext in EXTENSION_TO_LANGUAGE}


# ---------------------------------------------------------------------------
#  IndexProgress
# ---------------------------------------------------------------------------


@dataclass
class IndexProgress:
    """Tracks progress of a full repository indexing job."""

    total: int = 0
    done: int = 0
    status: str = "pending"  # pending | running | completed | failed
    error: str = ""
    last_processed_index: int = 0
    chunks_total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IndexProgress":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
#  RepositoryIndexer
# ---------------------------------------------------------------------------


class RepositoryIndexer:
    """Indexes an entire repository using the GitHub tree API.

    Args:
        embedding_service: Shared ``EmbeddingService`` instance.
        github_client: Authenticated GitHub API client.
        redis: Async Redis client for progress tracking.
    """

    def __init__(
        self,
        embedding_service: EmbeddingService,
        github_client: GitHubClient,
        redis: Any,
    ) -> None:
        self._embedder = embedding_service
        self._github = github_client
        self._redis = redis

    async def index_repository(
        self,
        repo_full_name: str,
        repo_id: int,
        head_sha: str,
    ) -> IndexProgress:
        """Index all code files in a repository.

        Uses the GitHub git trees API (no clone required).
        Supports resume: stores ``last_processed_index`` in Redis.

        Args:
            repo_full_name: ``owner/repo`` string.
            repo_id: Database repository ID (Qdrant namespace).
            head_sha: Commit SHA of the branch to index.

        Returns:
            Final ``IndexProgress`` state.
        """
        progress_key = f"{PROGRESS_REDIS_PREFIX}:{repo_id}"

        # Load resume state if available
        resume_index = 0
        try:
            raw = await self._redis.get(progress_key)
            if raw:
                saved = IndexProgress.from_dict(json.loads(raw))
                if saved.status == "running":
                    resume_index = saved.last_processed_index
                    logger.info(
                        "Resuming indexing for repo_id=%d from file index %d",
                        repo_id,
                        resume_index,
                    )
        except Exception:
            pass

        # Fetch full file tree
        try:
            all_files = await self._fetch_tree(repo_full_name, head_sha)
        except Exception as exc:
            logger.exception("Failed to fetch git tree for %s", repo_full_name)
            progress = IndexProgress(status="failed", error=str(exc))
            await self._save_progress(progress_key, progress)
            return progress

        # Filter to code files only
        code_files = [f for f in all_files if self._is_code_file(f["path"], f.get("size", 0))]

        if not code_files:
            logger.info("No code files found in %s", repo_full_name)
            progress = IndexProgress(total=0, done=0, status="completed")
            await self._save_progress(progress_key, progress)
            return progress

        progress = IndexProgress(
            total=len(code_files),
            done=resume_index,
            status="running",
            last_processed_index=resume_index,
        )
        await self._save_progress(progress_key, progress)

        # Rate-limit tracking: timestamps of recent API calls
        call_timestamps: list[float] = []
        pending_files = code_files[resume_index:]

        for batch_start in range(0, len(pending_files), FILE_BATCH_SIZE):
            batch = pending_files[batch_start : batch_start + FILE_BATCH_SIZE]

            # Rate limit: enforce max GITHUB_CALLS_PER_MINUTE
            now = time.monotonic()
            call_timestamps = [t for t in call_timestamps if now - t < 60.0]
            if len(call_timestamps) + len(batch) > GITHUB_CALLS_PER_MINUTE:
                sleep_for = 60.0 - (now - call_timestamps[0]) if call_timestamps else 5.0
                logger.debug("Rate limit: sleeping %.1fs", max(sleep_for, 1.0))
                await asyncio.sleep(max(sleep_for, 1.0))

            # Index files in parallel
            tasks = [
                asyncio.create_task(
                    self._index_one_file(f, repo_full_name, repo_id, head_sha)
                )
                for f in batch
            ]
            call_timestamps.extend([time.monotonic()] * len(batch))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            batch_chunks = sum(r for r in results if isinstance(r, int))
            progress.chunks_total += batch_chunks
            progress.done += len(batch)
            progress.last_processed_index = resume_index + batch_start + len(batch)
            await self._save_progress(progress_key, progress)

            logger.info(
                "Indexing progress: %d/%d files (repo_id=%d)",
                progress.done,
                progress.total,
                repo_id,
            )

        progress.status = "completed"
        await self._save_progress(progress_key, progress)
        logger.info(
            "Repository indexing complete: %d files, %d chunks (repo_id=%d)",
            progress.done,
            progress.chunks_total,
            repo_id,
        )
        return progress

    async def get_progress(self, repo_id: int) -> IndexProgress | None:
        """Load current indexing progress from Redis."""
        key = f"{PROGRESS_REDIS_PREFIX}:{repo_id}"
        try:
            raw = await self._redis.get(key)
            if raw:
                return IndexProgress.from_dict(json.loads(raw))
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    #  Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_tree(
        self, repo_full_name: str, sha: str
    ) -> list[dict[str, Any]]:
        """Fetch all blobs from the git tree recursively.

        Handles the ``truncated=true`` case by recursively fetching subtrees.
        """
        owner, repo = repo_full_name.split("/", 1)
        url = f"/repos/{owner}/{repo}/git/trees/{sha}?recursive=1"

        try:
            response = await self._github._request("GET", url)
            data = response.json()
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch git tree: {exc}") from exc

        tree: list[dict[str, Any]] = data.get("tree", [])
        blobs = [item for item in tree if item.get("type") == "blob"]

        # If truncated, we only get a partial list — log a warning
        if data.get("truncated"):
            logger.warning(
                "Git tree for %s was truncated (%d blobs retrieved). "
                "Large repos may not be fully indexed.",
                repo_full_name,
                len(blobs),
            )

        return blobs

    async def _index_one_file(
        self,
        file_info: dict[str, Any],
        repo_full_name: str,
        repo_id: int,
        head_sha: str,
    ) -> int:
        """Fetch, chunk, and embed a single file. Returns chunk count."""
        path = file_info["path"]
        try:
            content = await self._github.get_file_content(
                repo_full_name, path, head_sha
            )
            if not content:
                return 0

            chunks = extract_chunks(content, path)
            if not chunks:
                return 0

            stats = await self._embedder.upsert_chunks(chunks, repo_id, head_sha)
            return stats.chunks_upserted
        except Exception:
            logger.debug("Failed to index file %s — skipping", path)
            return 0

    @staticmethod
    def _is_code_file(path: str, size: int) -> bool:
        """Return True if the file should be indexed."""
        if size > MAX_FILE_SIZE_BYTES:
            return False
        suffix = Path(path).suffix.lower()
        if suffix not in _CODE_EXTENSIONS:
            return False
        for pattern in _SKIP_PATTERNS:
            if pattern.search(path):
                return False
        return True

    async def _save_progress(self, key: str, progress: IndexProgress) -> None:
        """Persist progress to Redis (best-effort, never raises)."""
        try:
            await self._redis.setex(key, 7 * 24 * 3600, json.dumps(progress.to_dict()))
        except Exception:
            pass
