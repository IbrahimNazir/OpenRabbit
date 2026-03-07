"""Integration tests for the Phase 3 RAG/context engine.

Uses qdrant_client.QdrantClient(":memory:") for in-process Qdrant (no Docker needed).
Mocks openai.AsyncOpenAI to return deterministic unit vectors.

Covers ADR-0028 through ADR-0034.
"""

from __future__ import annotations

import json
import uuid
from hashlib import sha256
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from qdrant_client import AsyncQdrantClient

from app.parsing.chunk_extractor import CodeChunk
from app.rag.embedder import (
    EMBED_CACHE_TTL,
    QDRANT_COLLECTION,
    QDRANT_VECTOR_SIZE,
    EmbeddingService,
    _chunk_id_to_uuid,
)
from app.rag.retriever import SCORE_THRESHOLD, ContextRetriever


# ---------------------------------------------------------------------------
#  Test helpers & fixtures
# ---------------------------------------------------------------------------

_UNIT_VECTOR: list[float] = [1.0] + [0.0] * (QDRANT_VECTOR_SIZE - 1)
_ORTHOGONAL_VECTOR: list[float] = [0.0, 1.0] + [0.0] * (QDRANT_VECTOR_SIZE - 2)


def _fake_embed_response(vector: list[float]) -> Any:
    """Build a fake openai.AsyncOpenAI embedding response."""
    mock_data = MagicMock()
    mock_data.embedding = vector
    mock_response = MagicMock()
    mock_response.data = [mock_data]
    mock_response.usage.total_tokens = 10
    return mock_response


def _make_redis_mock() -> AsyncMock:
    """Return a minimal async Redis mock with get/setex returning None."""
    redis = AsyncMock()
    redis.get.return_value = None
    redis.setex.return_value = True
    return redis


async def _make_embedding_service(
    qdrant_client: AsyncQdrantClient,
    redis: AsyncMock,
    openai_vector: list[float] | None = None,
) -> EmbeddingService:
    """Create an EmbeddingService that uses an in-memory Qdrant client."""
    svc = EmbeddingService.__new__(EmbeddingService)
    svc._redis = redis
    svc._qdrant = qdrant_client
    svc._openai = MagicMock()
    svc._openai.embeddings = MagicMock()
    vector = openai_vector or _UNIT_VECTOR
    svc._openai.embeddings.create = AsyncMock(
        return_value=_fake_embed_response(vector)
    )
    await svc.ensure_collection()
    return svc


def _make_chunk(
    chunk_id: str = "abcd1234efgh5678",
    file_path: str = "src/auth.py",
    name: str = "authenticate",
    content: str = "def authenticate(user): ...",
    start_line: int = 10,
    end_line: int = 20,
    language: str = "python",
) -> CodeChunk:
    return CodeChunk(
        chunk_id=chunk_id,
        file_path=file_path,
        name=name,
        content=content,
        start_line=start_line,
        end_line=end_line,
        chunk_type="function",
        language=language,
    )


# ---------------------------------------------------------------------------
#  Test 1: embed_text cache miss → calls OpenAI, stores in Redis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_text_cache_miss_calls_openai() -> None:
    redis = _make_redis_mock()
    async with AsyncQdrantClient(location=":memory:") as qdrant:
        svc = await _make_embedding_service(qdrant, redis, _UNIT_VECTOR)

        vector = await svc.embed_text("hello world")

    assert vector == _UNIT_VECTOR
    svc._openai.embeddings.create.assert_called_once()
    redis.setex.assert_called_once()
    call_args = redis.setex.call_args
    assert call_args[0][1] == EMBED_CACHE_TTL


# ---------------------------------------------------------------------------
#  Test 2: embed_text cache hit → returns cached vector, no OpenAI call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_text_cache_hit_skips_openai() -> None:
    text = "hello world"
    cached_vector = _UNIT_VECTOR
    redis = _make_redis_mock()
    redis.get.return_value = json.dumps(cached_vector)

    async with AsyncQdrantClient(location=":memory:") as qdrant:
        svc = await _make_embedding_service(qdrant, redis)

        vector = await svc.embed_text(text)

    assert vector == cached_vector
    svc._openai.embeddings.create.assert_not_called()


# ---------------------------------------------------------------------------
#  Test 3: upsert_chunks + find_relevant_context returns matching chunk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_and_retrieve_chunks() -> None:
    redis = _make_redis_mock()
    repo_id = 42

    async with AsyncQdrantClient(location=":memory:") as qdrant:
        svc = await _make_embedding_service(qdrant, redis, _UNIT_VECTOR)
        chunks = [_make_chunk(content="def authenticate(user): pass")]
        await svc.upsert_chunks(chunks, repo_id=repo_id, commit_sha="abc123")

        retriever = ContextRetriever(embedding_service=svc)
        results = await retriever.find_relevant_context(
            query="user authentication",
            repo_id=repo_id,
            exclude_files=[],
        )

    assert len(results) >= 1
    assert results[0].file_path == "src/auth.py"
    assert results[0].name == "authenticate"


# ---------------------------------------------------------------------------
#  Test 4: score threshold filters low-similarity results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_threshold_filters_low_similarity() -> None:
    """A chunk indexed with _UNIT_VECTOR queried with _ORTHOGONAL_VECTOR has score ~0.
    It should be filtered out by the 0.75 score threshold."""
    redis = _make_redis_mock()
    repo_id = 99

    async with AsyncQdrantClient(location=":memory:") as qdrant:
        # Index chunk with unit vector
        svc_index = await _make_embedding_service(qdrant, redis, _UNIT_VECTOR)
        chunks = [_make_chunk()]
        await svc_index.upsert_chunks(chunks, repo_id=repo_id, commit_sha="sha1")

        # Query with orthogonal vector (cosine similarity ≈ 0)
        svc_query = svc_index
        svc_query._openai.embeddings.create = AsyncMock(
            return_value=_fake_embed_response(_ORTHOGONAL_VECTOR)
        )
        retriever = ContextRetriever(embedding_service=svc_query)
        results = await retriever.find_relevant_context(
            query="completely unrelated query",
            repo_id=repo_id,
            exclude_files=[],
        )

    assert results == []


# ---------------------------------------------------------------------------
#  Test 5: exclude_files filter removes chunk from results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exclude_files_filter() -> None:
    redis = _make_redis_mock()
    repo_id = 11

    async with AsyncQdrantClient(location=":memory:") as qdrant:
        svc = await _make_embedding_service(qdrant, redis, _UNIT_VECTOR)
        chunks = [_make_chunk(file_path="src/auth.py")]
        await svc.upsert_chunks(chunks, repo_id=repo_id, commit_sha="sha_x")

        retriever = ContextRetriever(embedding_service=svc)
        results = await retriever.find_relevant_context(
            query="auth",
            repo_id=repo_id,
            exclude_files=["src/auth.py"],
        )

    assert results == []


# ---------------------------------------------------------------------------
#  Test 6: PR indexer skips already-indexed chunks at same commit SHA
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_indexer_skips_already_indexed_chunks() -> None:
    from app.core.diff_parser import FileDiff
    from app.rag.pr_indexer import PRIndexer

    redis = _make_redis_mock()
    repo_id = 7

    async with AsyncQdrantClient(location=":memory:") as qdrant:
        svc = await _make_embedding_service(qdrant, redis, _UNIT_VECTOR)

        # Manually pre-index a chunk at "sha_head"
        chunk = _make_chunk(
            chunk_id="aaaa1111bbbb2222",
            file_path="app/auth.py",
            start_line=1,
            end_line=15,
        )
        await svc.upsert_chunks([chunk], repo_id=repo_id, commit_sha="sha_head")

        # Build a FileDiff that covers the same lines
        fd = MagicMock(spec=FileDiff)
        fd.filename = "app/auth.py"
        fd.status = "modified"
        fd.language = "python"
        hunk = MagicMock()
        hunk.new_start = 1
        hunk.new_count = 15
        fd.hunks = [hunk]

        # Patch extract_chunks to return the same chunk
        github_mock = AsyncMock()
        github_mock.get_file_content.return_value = "def authenticate(user): pass\n"

        with patch("app.rag.pr_indexer.extract_chunks", return_value=[chunk]):
            indexer = PRIndexer(embedding_service=svc, github_client=github_mock)
            embed_call_count_before = svc._openai.embeddings.create.call_count
            await indexer.index_pr_changes([fd], repo_id, "sha_head", "owner/repo")
            embed_call_count_after = svc._openai.embeddings.create.call_count

    # No new embed calls since chunk is already at sha_head
    assert embed_call_count_after == embed_call_count_before


# ---------------------------------------------------------------------------
#  Test 7: find_callers returns chunks containing the function name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_callers_keyword_search() -> None:
    redis = _make_redis_mock()
    repo_id = 55

    async with AsyncQdrantClient(location=":memory:") as qdrant:
        svc = await _make_embedding_service(qdrant, redis, _UNIT_VECTOR)
        caller_chunk = _make_chunk(
            chunk_id="cccc3333dddd4444",
            file_path="app/views.py",
            name="login_view",
            content="result = authenticate(request.user)",
        )
        await svc.upsert_chunks([caller_chunk], repo_id=repo_id, commit_sha="sha2")

        retriever = ContextRetriever(embedding_service=svc)
        results = await retriever.find_callers(
            function_name="authenticate",
            repo_id=repo_id,
            exclude_files=[],
        )

    assert len(results) >= 1
    assert any(r.file_path == "app/views.py" for r in results)


# ---------------------------------------------------------------------------
#  Test 8: RAG failure does not crash the review pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_failure_does_not_crash_pipeline() -> None:
    """Mock Qdrant to raise on every call; pipeline must still return findings."""
    from unittest.mock import patch as _patch

    from app.core.diff_parser import FileDiff
    from app.rag.context_builder import ContextBuilder
    from app.rag.retriever import ContextRetriever

    redis = _make_redis_mock()

    # Build a retriever whose find_relevant_context always raises
    svc_mock = MagicMock(spec=EmbeddingService)
    svc_mock.embed_text = AsyncMock(side_effect=RuntimeError("Qdrant down"))
    retriever = ContextRetriever(embedding_service=svc_mock)

    llm_mock = AsyncMock()
    builder = ContextBuilder(retriever=retriever, llm_client=llm_mock, redis=redis)

    fd = MagicMock(spec=FileDiff)
    fd.filename = "app/main.py"
    fd.language = "python"
    fd.hunks = []

    # Should return empty EnrichedContext without raising
    ctx = await builder.build_review_context(fd, repo_id=1)

    assert ctx.file_diff is fd
    assert ctx.relevant_chunks == []
    assert ctx.caller_chunks == []
    assert ctx.past_findings == []


# ---------------------------------------------------------------------------
#  Test 9: context builder trims chunks to token budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_builder_trimming() -> None:
    from app.rag.context_builder import MAX_CONTEXT_TOKENS, _trim_to_token_budget
    from app.rag.retriever import RetrievedChunk

    # Create chunks that together exceed the budget
    # 4000 tokens budget; each chunk ~500 words ≈ ~375 tokens
    big_content = "word " * 500  # ~500 tokens per chunk
    chunks = [
        RetrievedChunk(
            chunk_id=f"chunk{i:04d}",
            file_path=f"file{i}.py",
            name=f"func{i}",
            content=big_content,
            score=1.0 - i * 0.05,
            start_line=1,
            end_line=30,
            chunk_type="function",
            language="python",
        )
        for i in range(20)
    ]

    included, total_tokens = _trim_to_token_budget(chunks, MAX_CONTEXT_TOKENS)

    assert total_tokens <= MAX_CONTEXT_TOKENS
    assert len(included) < len(chunks)
    # Highest-scored chunks should be first
    assert included[0].score >= included[-1].score


# ---------------------------------------------------------------------------
#  Test 10: _describe_change uses Redis cache and falls back on LLM error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_describe_change_cache_and_fallback() -> None:
    from app.core.diff_parser import FileDiff
    from app.rag.context_builder import ContextBuilder
    from app.rag.retriever import ContextRetriever

    # --- Part A: cache hit returns without calling LLM ---
    cached_desc = "Adds rate limiting to the authenticate function"
    redis_hit = _make_redis_mock()
    redis_hit.get.return_value = cached_desc

    svc_mock = MagicMock(spec=EmbeddingService)
    retriever = ContextRetriever(embedding_service=svc_mock)
    llm_mock = AsyncMock()
    llm_mock.complete = AsyncMock(return_value=(cached_desc, 0.0))

    builder = ContextBuilder(retriever=retriever, llm_client=llm_mock, redis=redis_hit)

    fd = MagicMock(spec=FileDiff)
    fd.filename = "app/auth.py"
    fd.language = "python"
    hunk = MagicMock()
    hunk.lines = [MagicMock(line_type="added", content="    return True")]
    fd.hunks = [hunk]

    result = await builder._describe_change(fd)
    assert result == cached_desc
    llm_mock.complete.assert_not_called()

    # --- Part B: LLM error falls back to raw hunk text ---
    redis_miss = _make_redis_mock()
    redis_miss.get.return_value = None

    llm_failing = AsyncMock()
    llm_failing.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

    builder2 = ContextBuilder(retriever=retriever, llm_client=llm_failing, redis=redis_miss)
    result2 = await builder2._describe_change(fd)

    # Should return a non-empty fallback string (raw hunk text), not raise
    assert isinstance(result2, str)
    assert len(result2) > 0
