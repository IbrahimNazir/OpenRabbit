"""Shared pytest fixtures for OpenRabbit tests.

This module contains all reusable fixtures for unit, integration, and E2E tests.
Fixtures are organized by category: mocks, in-memory services, sample data.
"""

from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# Patch SQLite JSONB support BEFORE importing models
from sqlalchemy.dialects.sqlite import base as sqlite_dialect
from sqlalchemy import event

# Register a type compiler method on the SQLite type compiler class
def visit_JSONB(self, type_, **kw):
    """Compile JSONB as JSON for SQLite."""
    return self.visit_JSON(type_, **kw)

sqlite_dialect.SQLiteTypeCompiler.visit_JSONB = visit_JSONB

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config_loader import ReviewConfig
from app.models.database import Base
from tests.fixtures.sample_diffs import (
    BOT_PR_DIFF,
    SECURITY_PYTHON_DIFF,
    SIMPLE_PYTHON_DIFF,
    TYPESCRIPT_DIFF,
)


# =============================================================================
#  Mock GitHub Client
# =============================================================================


@pytest.fixture
def mock_github_client() -> AsyncMock:
    """Mock GitHub API client with pre-configured returns.

    Returns a GitHub client that:
    - Returns sample diffs
    - Returns file content
    - Accepts review/comment posts
    - Tracks call counts for assertions
    """
    client = AsyncMock()
    client.get_pr_diff = AsyncMock(return_value=SIMPLE_PYTHON_DIFF)
    client.get_file_content = AsyncMock(return_value="# sample file content\n")
    client.post_review = AsyncMock(return_value={"id": 1})
    client.post_review_comment = AsyncMock(return_value={"id": 2})
    client.get_access_token = AsyncMock(return_value="ghs_test_token_123")
    return client


# =============================================================================
#  Mock LLM Clients
# =============================================================================


@pytest.fixture
def mock_llm_client() -> AsyncMock:
    """Mock LLM client that returns safe, realistic responses.

    Dispatches on prompt content:
    - summary/summarize → low-risk summary
    - synthesis/remove_duplicates → keep all findings
    - bug/security → empty findings (no actual bugs)
    """
    client = AsyncMock()

    async def _complete_with_json(prompt: str, **kwargs: Any) -> tuple[Any, float]:
        # Detect prompt type by content
        if (
            "summary" in prompt.lower()
            or "summarize" in prompt.lower()
            or "key_changes" in prompt.lower()
        ):
            return (
                {
                    "summary": "Adds a discount calculation utility function.",
                    "key_changes": ["New calculate_discount function"],
                    "risk_level": "low",
                },
                0.001,
            )
        if "synthesis" in prompt.lower() or "remove_duplicates" in prompt.lower():
            # Return all findings as-is
            max_id = 0
            for m in re.finditer(r'"id":\s*(\d+)', prompt):
                max_id = max(max_id, int(m.group(1)))
            return (
                {
                    "keep": list(range(max_id + 1)),
                    "remove_duplicates": [],
                    "false_positives": [],
                    "final_summary": "Review complete.",
                },
                0.001,
            )
        # Bug/style detection: return empty by default
        return [], 0.001

    client.complete_with_json = _complete_with_json
    client.complete = AsyncMock(return_value=("Generated text", 0.001))
    return client


@pytest.fixture
def mock_security_llm_client() -> AsyncMock:
    """Mock LLM client that flags security issues (SQL injection).

    Used for security PR tests that should produce critical findings.
    """
    client = AsyncMock()

    async def _complete_with_json(prompt: str, **kwargs: Any) -> tuple[Any, float]:
        if (
            "key_changes" in prompt
            or "summarize" in prompt.lower()
            or "summary" in prompt.lower()
        ):
            return (
                {
                    "summary": "Adds user lookup by username with SQL concatenation.",
                    "key_changes": ["New get_user_by_name function with SQL query"],
                    "risk_level": "high",
                },
                0.001,
            )
        if "synthesis" in prompt.lower() or "remove_duplicates" in prompt.lower():
            max_id = 0
            for m in re.finditer(r'"id":\s*(\d+)', prompt):
                max_id = max(max_id, int(m.group(1)))
            return (
                {
                    "keep": list(range(max_id + 1)),
                    "remove_duplicates": [],
                    "false_positives": [],
                    "final_summary": "SQL injection vulnerability found.",
                },
                0.001,
            )
        if "bug" in prompt.lower() or "security" in prompt.lower() or "queries.py" in prompt:
            return (
                [
                    {
                        "line_start": 7,
                        "line_end": 7,
                        "severity": "critical",
                        "category": "security",
                        "title": "SQL injection vulnerability",
                        "body": "User input is directly interpolated into SQL query. Use parameterized queries.",
                        "suggestion_code": None,
                    }
                ],
                0.002,
            )
        return [], 0.001

    client.complete_with_json = _complete_with_json
    client.complete = AsyncMock(return_value=("Generated text", 0.001))
    return client


# =============================================================================
#  Mock Redis
# =============================================================================


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Mock Redis client for cache tests.

    Returns:
    - get() → None (cache miss by default)
    - setex() → True
    - delete() → True
    """
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=True)
    redis.incr = AsyncMock(return_value=1)
    redis.ttl = AsyncMock(return_value=3600)
    return redis


# =============================================================================
#  In-Memory Qdrant
# =============================================================================


@pytest.fixture
async def in_memory_qdrant() -> Any:
    """In-memory Qdrant instance for RAG tests.

    Usage in test:
        async def test_something(in_memory_qdrant):
            client = in_memory_qdrant  # Already initialized
    """
    try:
        from qdrant_client.async_client import AsyncQdrantClient
    except ImportError:
        pytest.skip("qdrant-client not installed")

    client = AsyncQdrantClient(location=":memory:")
    yield client
    await client.close()


# =============================================================================
#  Test Database Session (SQLite)
# =============================================================================


@pytest.fixture
async def test_db_session() -> Any:
    """In-memory SQLite async session for database tests.

    This fixture:
    1. Creates an in-memory SQLite engine
    2. Removes server defaults that SQLite can't handle (gen_random_uuid)
    3. Creates all tables via Base.metadata (with JSONB → JSON type mapping)
    4. Yields an AsyncSession
    5. Rolls back after each test

    Usage:
        @pytest.mark.asyncio
        async def test_something(test_db_session):
            session = test_db_session
            # Use session.add(), session.execute(), etc.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # Remove server defaults that SQLite doesn't support
    @event.listens_for(Base.metadata, "before_create")
    def _remove_sqlite_unsupported_defaults(metadata, connection, **kw):
        """Remove gen_random_uuid() defaults for SQLite compatibility."""
        import uuid as uuid_module
        for table in metadata.sorted_tables:
            for column in table.columns:
                if column.server_default is not None:
                    if "gen_random_uuid" in str(column.server_default.arg):
                        column.server_default = None

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create session factory
    from sqlalchemy.ext.asyncio import AsyncSession as AsyncSessionClass
    from sqlalchemy.orm import sessionmaker

    async_session_factory = sessionmaker(
        engine, class_=AsyncSessionClass, expire_on_commit=False
    )

    async with async_session_factory() as session:
        yield session
        await session.rollback()

    await engine.dispose()


@pytest.fixture
def test_sync_db_session() -> Any:
    """Sync SQLite session for Celery task tests.

    Similar to test_db_session but sync for use in sync contexts.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    # Create tables
    Base.metadata.create_all(engine)

    # Create session
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()

    yield session

    session.rollback()
    session.close()
    engine.dispose()


# =============================================================================
#  Sample Data & Config
# =============================================================================


@pytest.fixture
def review_config() -> ReviewConfig:
    """Default ReviewConfig for tests."""
    return ReviewConfig(enabled=True)


@pytest.fixture
def sample_diffs() -> dict[str, str]:
    """Dictionary of sample diff strings for various PR scenarios."""
    return {
        "simple_python": SIMPLE_PYTHON_DIFF,
        "security_python": SECURITY_PYTHON_DIFF,
        "bot_pr": BOT_PR_DIFF,
        "typescript": TYPESCRIPT_DIFF,
    }


# =============================================================================
#  Embedding Service (with mocked HF API)
# =============================================================================


@pytest.fixture
async def embedding_service(in_memory_qdrant: Any, mock_redis: AsyncMock) -> Any:
    """Pre-wired EmbeddingService with in-memory Qdrant and mocked HF API.

    Usage:
        async def test_embeddings(embedding_service, mock_hf_embeddings):
            result = await embedding_service.upsert_chunks(...)
    """
    from app.rag.embedder import EmbeddingService

    # Mock the HF API call
    with pytest_mock() if False else None:  # Placeholder for actual mock if needed
        service = EmbeddingService(
            redis=mock_redis,
            qdrant_url=None,  # Will use in-memory client
            huggingface_api_key="hf_test_key",
        )
        # Override the qdrant client to use the in-memory one
        service.qdrant = in_memory_qdrant
        yield service
        await service.aclose()


# =============================================================================
#  Pytest Configuration
# =============================================================================


def pytest_configure(config: Any) -> None:
    """Register custom pytest markers."""
    config.addinivalue_line("markers", "unit: unit tests (no I/O)")
    config.addinivalue_line("markers", "integration: integration tests (in-memory services)")
    config.addinivalue_line("markers", "e2e: end-to-end tests (requires Docker services)")
    config.addinivalue_line("markers", "slow: tests that take > 5 seconds")
