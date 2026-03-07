"""Context builder: assembles enriched review context for each file diff.

Implements ADR-0033 (NL query construction) and ADR-0034 (past findings memory).

For each FileDiff:
1. Generate a natural language description of the change (micro-LLM call, cached)
2. Retrieve semantically similar code from the full repo (``find_relevant_context``)
3. Find callers of changed functions (``find_callers``)
4. Retrieve similar past findings as few-shot examples (``_find_past_findings``)
5. Trim total context to 4000 tokens

All operations degrade gracefully: any exception returns empty results rather
than propagating to the review pipeline.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from qdrant_client.http import models as qdrant_models

from app.core.diff_parser import FileDiff
from app.llm.prompts import PROMPT_DESCRIBE_CHANGE
from app.rag.embedder import PAST_FINDINGS_COLLECTION, EmbeddingService
from app.rag.retriever import ContextRetriever, RetrievedChunk

if TYPE_CHECKING:
    from app.core.comment_formatter import Finding
    from app.llm.client import LLMClient

logger = logging.getLogger(__name__)

MAX_CONTEXT_TOKENS = 4000
DESCRIPTION_CACHE_TTL = 7 * 24 * 3600  # 7 days


# ---------------------------------------------------------------------------
#  Data classes
# ---------------------------------------------------------------------------


@dataclass
class PastFinding:
    """A past review finding retrieved as a few-shot example."""

    category: str
    severity: str
    title: str
    body: str
    score: float


@dataclass
class EnrichedContext:
    """RAG-enriched context for a single file in the review."""

    file_diff: FileDiff
    relevant_chunks: list[RetrievedChunk] = field(default_factory=list)
    caller_chunks: list[RetrievedChunk] = field(default_factory=list)
    past_findings: list[PastFinding] = field(default_factory=list)
    total_tokens: int = 0


# ---------------------------------------------------------------------------
#  ContextBuilder
# ---------------------------------------------------------------------------


class ContextBuilder:
    """Builds enriched context for review stages.

    Args:
        retriever: ``ContextRetriever`` for Qdrant queries.
        llm_client: LLM client used for NL change description.
        redis: Async Redis client for NL description caching.
    """

    def __init__(
        self,
        retriever: ContextRetriever,
        llm_client: "LLMClient",
        redis: Any,
    ) -> None:
        self.retriever = retriever
        self._llm = llm_client
        self._redis = redis

    async def build_review_context(
        self,
        file_diff: FileDiff,
        repo_id: int,
    ) -> EnrichedContext:
        """Build enriched context for a single file diff.

        Args:
            file_diff: The parsed file diff.
            repo_id: Qdrant namespace for the repository.

        Returns:
            ``EnrichedContext`` populated with retrieved chunks and past findings.
            Never raises — any failure returns an empty EnrichedContext.
        """
        ctx = EnrichedContext(file_diff=file_diff)
        if repo_id == 0:
            return ctx

        # 1. Generate NL description of the change
        query = await self._describe_change(file_diff)

        # 2. Semantic similarity search
        ctx.relevant_chunks = await self.retriever.find_relevant_context(
            query=query,
            repo_id=repo_id,
            exclude_files=[file_diff.filename],
        )

        # 3. Find callers of changed functions (de-duplicated by chunk_id)
        changed_func_names = _extract_changed_function_names(file_diff)
        seen_chunk_ids: set[str] = set()
        for func_name in changed_func_names:
            callers = await self.retriever.find_callers(
                function_name=func_name,
                repo_id=repo_id,
                exclude_files=[file_diff.filename],
            )
            for c in callers:
                if c.chunk_id not in seen_chunk_ids:
                    seen_chunk_ids.add(c.chunk_id)
                    ctx.caller_chunks.append(c)

        # 4. Similar past findings (few-shot)
        ctx.past_findings = await self._find_past_findings(file_diff, repo_id)

        # 5. Trim to token budget
        ctx.relevant_chunks, ctx.total_tokens = _trim_to_token_budget(
            ctx.relevant_chunks, MAX_CONTEXT_TOKENS
        )

        return ctx

    async def upsert_past_finding(
        self,
        repo_id: int,
        org_id: int,
        finding: "Finding",
        was_applied: bool,
        was_dismissed: bool,
    ) -> None:
        """Store a review finding as a future few-shot example.

        Never raises — failures are logged as warnings.
        """
        try:
            vector = await self.retriever.embedding_service.embed_text(finding.body)
            point = qdrant_models.PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "repo_id": repo_id,
                    "org_id": org_id,
                    "category": finding.category,
                    "severity": finding.severity,
                    "title": finding.title,
                    "body": finding.body,
                    "language": "",  # enriched in Phase 4 when file_path is tracked
                    "was_applied": was_applied,
                    "was_dismissed": was_dismissed,
                },
            )
            await self.retriever.embedding_service.upsert_points_to_collection(
                PAST_FINDINGS_COLLECTION, [point]
            )
        except Exception:
            logger.warning(
                "Failed to upsert past finding '%s' — skipping", finding.title[:50]
            )

    # ------------------------------------------------------------------
    #  Internal helpers
    # ------------------------------------------------------------------

    async def _describe_change(self, file_diff: FileDiff) -> str:
        """Generate a one-sentence NL description of the change for embedding queries.

        Implements ADR-0033: micro-LLM call with 7-day Redis cache.
        Falls back to raw hunk text on any error.
        """
        hunk_text = _build_hunk_text(file_diff)
        if not hunk_text:
            return file_diff.filename

        cache_key = f"desc:{sha256(hunk_text.encode()).hexdigest()[:16]}"
        try:
            cached = await self._redis.get(cache_key)
            if cached:
                return str(cached)
        except Exception:
            pass

        try:
            description, _ = await self._llm.complete(
                PROMPT_DESCRIBE_CHANGE.format(
                    language=file_diff.language or "text",
                    hunk_content=hunk_text[:500],
                ),
                max_tokens=100,
            )
            description = description.strip()
        except Exception:
            logger.debug(
                "NL description generation failed for %s — using raw text",
                file_diff.filename,
            )
            return hunk_text[:200]

        try:
            await self._redis.setex(cache_key, DESCRIPTION_CACHE_TTL, description)
        except Exception:
            pass

        return description

    async def _find_past_findings(
        self,
        file_diff: FileDiff,
        repo_id: int,
        top_k: int = 3,
    ) -> list[PastFinding]:
        """Retrieve similar past findings as few-shot examples."""
        try:
            func_names = _extract_changed_function_names(file_diff)
            query = f"{file_diff.language or ''} {' '.join(func_names[:3])}".strip()
            if not query:
                return []

            query_vector = await self.retriever.embedding_service.embed_text(query)

            hits = await self.retriever.embedding_service.search(
                query_vector=query_vector,
                collection_name=PAST_FINDINGS_COLLECTION,
                search_filter=qdrant_models.Filter(
                    must=[
                        qdrant_models.FieldCondition(
                            key="repo_id",
                            match=qdrant_models.MatchValue(value=repo_id),
                        )
                    ]
                ),
                limit=top_k,
            )

            return [
                PastFinding(
                    category=str(h.payload.get("category", "")),
                    severity=str(h.payload.get("severity", "")),
                    title=str(h.payload.get("title", "")),
                    body=str(h.payload.get("body", "")),
                    score=h.score,
                )
                for h in hits
                if h.payload
            ]
        except Exception:
            logger.debug(
                "Past findings retrieval failed for %s — returning empty",
                file_diff.filename,
            )
            return []


# ---------------------------------------------------------------------------
#  Module-level helpers
# ---------------------------------------------------------------------------


def _build_hunk_text(file_diff: FileDiff) -> str:
    """Extract added lines from the first hunk, truncated to 500 chars."""
    for hunk in file_diff.hunks:
        lines = [
            dl.content
            for dl in hunk.lines
            if dl.line_type == "added"
        ]
        if lines:
            return "\n".join(lines)[:500]
    return ""


def _extract_changed_function_names(file_diff: FileDiff) -> list[str]:
    """Extract function/class names from AST context of changed hunks."""
    names: list[str] = []
    seen: set[str] = set()
    for hunk in file_diff.hunks:
        fn = getattr(hunk, "ast_function_context", None)
        if fn and fn not in seen:
            seen.add(fn)
            names.append(fn)
    return names


def _trim_to_token_budget(
    chunks: list[RetrievedChunk],
    budget: int,
) -> tuple[list[RetrievedChunk], int]:
    """Trim chunks to fit within a token budget, keeping highest-scored first.

    Uses tiktoken cl100k_base for token counting.
    """
    try:
        import tiktoken  # lazy import — same pattern as chunk_extractor.py

        enc = tiktoken.get_encoding("cl100k_base")
    except Exception:
        # tiktoken unavailable — return all chunks with approximate count
        return chunks, sum(len(c.content) // 4 for c in chunks)

    # Sort by score descending
    sorted_chunks = sorted(chunks, key=lambda c: c.score, reverse=True)
    included: list[RetrievedChunk] = []
    total_tokens = 0

    for chunk in sorted_chunks:
        chunk_tokens = len(enc.encode(chunk.content))
        if total_tokens + chunk_tokens > budget:
            break
        included.append(chunk)
        total_tokens += chunk_tokens

    return included, total_tokens
