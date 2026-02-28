"""Database engine, session factories, and lifecycle helpers.

Implements the dual-engine pattern from ADR-0006:
- Async engine (asyncpg) → used by FastAPI handlers
- Sync engine (psycopg2) → used by Celery workers
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Declarative Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base for all ORM models."""


# ---------------------------------------------------------------------------
#  Async engine (FastAPI)
# ---------------------------------------------------------------------------

_async_engine: Any = None
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


async def init_db() -> None:
    """Create the async engine and session factory.  Called during FastAPI lifespan startup."""
    global _async_engine, AsyncSessionLocal  # noqa: PLW0603

    settings = get_settings()
    _async_engine = create_async_engine(
        settings.database_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        echo=False,
    )
    AsyncSessionLocal = async_sessionmaker(
        _async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    logger.info("Async database engine initialized")


async def close_db() -> None:
    """Dispose the async engine.  Called during FastAPI lifespan shutdown."""
    global _async_engine  # noqa: PLW0603
    if _async_engine is not None:
        await _async_engine.dispose()
        _async_engine = None
        logger.info("Async database engine disposed")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async DB session with auto-commit / rollback."""
    if AsyncSessionLocal is None:
        raise RuntimeError("Database not initialized — call init_db() first")

    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
#  Sync engine (Celery workers)
# ---------------------------------------------------------------------------

_sync_engine: Any = None
SyncSessionLocal: sessionmaker[Session] | None = None


def init_sync_db() -> None:
    """Create the sync engine for Celery workers.  Called at worker startup."""
    global _sync_engine, SyncSessionLocal  # noqa: PLW0603

    settings = get_settings()
    _sync_engine = create_engine(
        settings.sync_database_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
    )
    SyncSessionLocal = sessionmaker(bind=_sync_engine)
    logger.info("Sync database engine initialized (Celery workers)")


def get_sync_db() -> Session:
    """Return a sync DB session for a Celery task."""
    if SyncSessionLocal is None:
        init_sync_db()
        assert SyncSessionLocal is not None
    return SyncSessionLocal()
