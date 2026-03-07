"""Semantic and keyword context retrieval from Qdrant.

Implements ADR-0032: two retrieval modes
1. ``find_relevant_context`` — vector similarity search (score threshold 0.75)
2. ``find_callers`` — keyword scroll using Qdrant text payload index

All methods return empty lists on failure (graceful degradation — never
propagates exceptions to the review pipeline).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from qdrant_client.http import models as qdrant_models

from app.rag.embedder import QDRANT_COLLECTION, EmbeddingService

logger = logging.getLogger(__name__)

SCORE_THRESHOLD = 0.75
DEFAULT_TOP_K = 5
MAX_CALLERS = 10


# ---------------------------------------------------------------------------
#  Data classes
# ---------------------------------------------------------------------------


@dataclass
class RetrievedChunk:
    """A code chunk retrieved from Qdrant."""

    chunk_id: str
    file_path: str
    name: str
    content: str
    score: float
    start_line: int
    end_line: int
    chunk_type: str
    language: str


# ---------------------------------------------------------------------------
#  ContextRetriever
# ---------------------------------------------------------------------------


class ContextRetriever:
    """Retrieves semantically and structurally related code from Qdrant.

    Args:
        embedding_service: Shared ``EmbeddingService`` instance.
    """

    def __init__(self, embedding_service: EmbeddingService) -> None:
        self._embedder = embedding_service

    async def find_relevant_context(
        self,
        query: str,
        repo_id: int,
        exclude_files: list[str],
        top_k: int = DEFAULT_TOP_K,
    ) -> list[RetrievedChunk]:
        """Find semantically similar code chunks via vector search.

        Args:
            query: Natural language description of the change.
            repo_id: Qdrant namespace for the repository.
            exclude_files: File paths to exclude (already in the PR diff).
            top_k: Maximum number of results to return.

        Returns:
            Up to ``top_k`` chunks with score >= SCORE_THRESHOLD.
        """
        try:
            query_vector = await self._embedder.embed_text(query)

            must_conditions: list[qdrant_models.FieldCondition] = [
                qdrant_models.FieldCondition(
                    key="repo_id",
                    match=qdrant_models.MatchValue(value=repo_id),
                )
            ]
            must_not_conditions: list[qdrant_models.FieldCondition] = []
            if exclude_files:
                must_not_conditions.append(
                    qdrant_models.FieldCondition(
                        key="file_path",
                        match=qdrant_models.MatchAny(any=exclude_files),
                    )
                )

            search_filter = qdrant_models.Filter(
                must=must_conditions,
                must_not=must_not_conditions if must_not_conditions else None,
            )

            hits = await self._embedder.search(
                query_vector=query_vector,
                collection_name=QDRANT_COLLECTION,
                search_filter=search_filter,
                limit=top_k * 2,  # fetch extra, then filter by score
            )

            results: list[RetrievedChunk] = []
            for hit in hits:
                if hit.score < SCORE_THRESHOLD:
                    continue
                if not hit.payload:
                    continue
                results.append(_payload_to_chunk(hit.payload, hit.score))
                if len(results) >= top_k:
                    break

            return results

        except Exception:
            logger.warning(
                "find_relevant_context failed for repo_id=%d — returning empty",
                repo_id,
            )
            return []

    async def find_callers(
        self,
        function_name: str,
        repo_id: int,
        exclude_files: list[str],
    ) -> list[RetrievedChunk]:
        """Find code chunks that call or reference a function by name.

        Uses Qdrant's text payload index on the ``content`` field for keyword
        matching (ADR-0032).  Falls back gracefully if the text index is not
        available.

        Args:
            function_name: The function name to search for.
            repo_id: Qdrant namespace for the repository.
            exclude_files: File paths to exclude.

        Returns:
            Up to MAX_CALLERS chunks containing the function name.
        """
        try:
            must_conditions: list[qdrant_models.FieldCondition] = [
                qdrant_models.FieldCondition(
                    key="repo_id",
                    match=qdrant_models.MatchValue(value=repo_id),
                ),
                qdrant_models.FieldCondition(
                    key="content",
                    match=qdrant_models.MatchText(text=function_name),
                ),
            ]
            must_not_conditions: list[qdrant_models.FieldCondition] = []
            if exclude_files:
                must_not_conditions.append(
                    qdrant_models.FieldCondition(
                        key="file_path",
                        match=qdrant_models.MatchAny(any=exclude_files),
                    )
                )

            scroll_filter = qdrant_models.Filter(
                must=must_conditions,
                must_not=must_not_conditions if must_not_conditions else None,
            )

            records = await self._embedder.scroll(
                collection_name=QDRANT_COLLECTION,
                scroll_filter=scroll_filter,
                limit=MAX_CALLERS,
            )

            results = [
                _payload_to_chunk(r.payload, 1.0)
                for r in records
                if r.payload
            ]

            # Sort by start_line for readability
            results.sort(key=lambda c: (c.file_path, c.start_line))
            return results[:MAX_CALLERS]

        except Exception:
            logger.warning(
                "find_callers failed for '%s' in repo_id=%d — returning empty",
                function_name,
                repo_id,
            )
            return []


# ---------------------------------------------------------------------------
#  Helper
# ---------------------------------------------------------------------------


def _payload_to_chunk(payload: dict[str, object], score: float) -> RetrievedChunk:
    """Convert a Qdrant payload dict to a RetrievedChunk."""
    return RetrievedChunk(
        chunk_id=str(payload.get("chunk_id", "")),
        file_path=str(payload.get("file_path", "")),
        name=str(payload.get("name", "")),
        content=str(payload.get("content", "")),
        score=score,
        start_line=int(payload.get("start_line") or 0),
        end_line=int(payload.get("end_line") or 0),
        chunk_type=str(payload.get("chunk_type", "unknown")),
        language=str(payload.get("language", "")),
    )
