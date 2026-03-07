"""Incremental PR-time chunk upsert.

Implements ADR-0031: on each PR review, re-embed only the chunks that overlap
with changed hunks.  Unchanged chunks at the same commit SHA are skipped.

This keeps per-PR embedding costs near zero after initial indexing.
"""

from __future__ import annotations

import logging

from app.core.diff_parser import DiffHunk, FileDiff
from app.core.github_client import GitHubClient
from app.parsing.chunk_extractor import CodeChunk, extract_chunks
from app.rag.embedder import EmbeddingService

logger = logging.getLogger(__name__)


class PRIndexer:
    """Incrementally indexes only the changed parts of a PR.

    Args:
        embedding_service: Shared ``EmbeddingService`` instance.
        github_client: Authenticated GitHub API client.
    """

    def __init__(
        self,
        embedding_service: EmbeddingService,
        github_client: GitHubClient,
    ) -> None:
        self._embedder = embedding_service
        self._github = github_client

    async def index_pr_changes(
        self,
        file_diffs: list[FileDiff],
        repo_id: int,
        head_sha: str,
        repo_full_name: str,
    ) -> int:
        """Index only chunks that overlap with changed hunks in this PR.

        Args:
            file_diffs: Parsed file diffs from the PR.
            repo_id: Database repository ID (used as Qdrant namespace).
            head_sha: HEAD commit SHA of the PR branch.
            repo_full_name: ``owner/repo`` string for GitHub API calls.

        Returns:
            Total number of chunks upserted.
        """
        total_upserted = 0

        for file_diff in file_diffs:
            if file_diff.status == "removed":
                try:
                    await self._embedder.delete_file_chunks(repo_id, file_diff.filename)
                    logger.debug("Deleted chunks for removed file: %s", file_diff.filename)
                except Exception:
                    logger.warning(
                        "Could not delete chunks for removed file %s", file_diff.filename
                    )
                continue

            if file_diff.status not in ("added", "modified", "renamed"):
                continue

            try:
                content = await self._github.get_file_content(
                    repo_full_name, file_diff.filename, head_sha
                )
            except Exception:
                logger.debug(
                    "Could not fetch content for %s — skipping incremental index",
                    file_diff.filename,
                )
                continue

            try:
                all_chunks = extract_chunks(content, file_diff.filename)
            except Exception:
                logger.debug("Chunk extraction failed for %s", file_diff.filename)
                continue

            # Filter: keep only chunks that overlap with changed hunks
            changed_chunks = [
                c for c in all_chunks if self._overlaps_hunks(c, file_diff.hunks)
            ]
            if not changed_chunks:
                continue

            # Skip chunks already indexed at this exact commit SHA
            try:
                existing_ids = await self._embedder.scroll_chunk_ids_for_file(
                    repo_id, file_diff.filename, head_sha
                )
            except Exception:
                existing_ids = set()

            new_chunks = [c for c in changed_chunks if c.chunk_id not in existing_ids]
            if not new_chunks:
                logger.debug(
                    "All %d changed chunks for %s already indexed at %s",
                    len(changed_chunks),
                    file_diff.filename,
                    head_sha[:8],
                )
                continue

            try:
                stats = await self._embedder.upsert_chunks(new_chunks, repo_id, head_sha)
                total_upserted += stats.chunks_upserted
                logger.debug(
                    "Indexed %d chunks for %s (skipped %d already current)",
                    stats.chunks_upserted,
                    file_diff.filename,
                    len(changed_chunks) - len(new_chunks),
                )
            except Exception:
                logger.warning(
                    "Failed to upsert chunks for %s — continuing", file_diff.filename
                )

        logger.info(
            "PR incremental indexing complete: %d chunks upserted (repo_id=%d)",
            total_upserted,
            repo_id,
        )
        return total_upserted

    @staticmethod
    def _overlaps_hunks(chunk: CodeChunk, hunks: list[DiffHunk]) -> bool:
        """Return True if chunk's line range overlaps any changed hunk."""
        for hunk in hunks:
            hunk_end = hunk.new_start + max(hunk.new_count - 1, 0)
            if chunk.start_line <= hunk_end and chunk.end_line >= hunk.new_start:
                return True
        return False
