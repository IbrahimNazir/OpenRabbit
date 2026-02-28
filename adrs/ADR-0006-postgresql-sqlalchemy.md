# ADR-0006: PostgreSQL + SQLAlchemy 2.0 Async ORM

| Field | Value |
|-------|-------|
| **ID** | ADR-0006 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 1 — Project Foundation |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | database, orm, persistence, postgresql |

---

## Context and Problem Statement

OpenRabbit needs persistent storage for: GitHub App installation records (tenant data), repository metadata and indexing status, PR review job records, individual findings, and conversation thread state. The database is also a source of truth for idempotency checks (has this PR already been reviewed?) and cost tracking.

---

## Decision Drivers

1. **ACID transactions** — Review records must be consistent. A review marked "completed" must have all its findings persisted atomically.
2. **JSONB support** — Conversation thread state (arbitrary JSON) and per-tenant config (arbitrary JSON) are stored as JSONB columns for flexibility without schema migrations
3. **Async support** — The FastAPI layer needs async DB access for health checks and admin endpoints
4. **Self-hostable** — The database must run in Docker without a cloud dependency
5. **Migration support** — Schema changes across 20 days of development require a proper migration tool

---

## Decision

**Use PostgreSQL 16 with SQLAlchemy 2.0 in async mode, using asyncpg as the driver, managed by Alembic.**

### Why PostgreSQL over alternatives?

| Database | ACID | JSONB | Async Python | Self-host | Vector (future) |
|----------|------|-------|--------------|-----------|-----------------|
| **PostgreSQL 16** | ✅ | ✅ Native | ✅ asyncpg | ✅ Docker | ✅ pgvector |
| MySQL 8 | ✅ | ⚠️ Limited | ✅ aiomysql | ✅ Docker | ❌ |
| SQLite | ✅ | ❌ | ⚠️ aiosqlite | ✅ File | ❌ |
| MongoDB | ❌ | ✅ Native | ✅ motor | ✅ Docker | ❌ |

PostgreSQL's JSONB is specifically chosen for `thread_state` and `config` columns — we can query inside JSONB with indexes while retaining the flexibility to add fields without migrations.

### Why SQLAlchemy 2.0 over alternatives?

- **Raw SQL:** Rejected — no migration support, no ORM benefits, more code to maintain
- **SQLModel:** Rejected — built on SQLAlchemy, but adds another abstraction layer with less documentation; SQLAlchemy 2.0 directly is more stable for a long-lived open-source project
- **Tortoise ORM:** Rejected — smaller community, fewer contributors, less battle-tested
- **Databases (encode/databases):** Rejected — minimal ORM features, better for simple cases only

### Session Management Pattern

```python
# app/models/database.py
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

engine = create_async_engine(
    settings.database_url,  # postgresql+asyncpg://...
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,    # verify connection before use (handles DB restarts)
    echo=False,            # set True for SQL query logging during development
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # prevent lazy-loading errors after commit
)

# FastAPI dependency
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

### Celery Workers Database Access

Celery workers use **synchronous** SQLAlchemy (not async) because Celery tasks run in a synchronous context. This means we maintain two engine configurations:

```python
# app/models/database.py
# Async engine — used by FastAPI
async_engine = create_async_engine(settings.database_url)

# Sync engine — used by Celery workers
# Note: uses 'postgresql+psycopg2://' not 'postgresql+asyncpg://'
sync_engine = create_engine(settings.sync_database_url)
SyncSessionLocal = sessionmaker(bind=sync_engine)
```

This is a known SQLAlchemy 2.0 pattern and is fully supported.

---

## Consequences

### Positive
- JSONB columns allow `thread_state` to evolve without schema migrations — we can add fields to the conversation history structure without an Alembic migration
- PostgreSQL's `gen_random_uuid()` function generates UUID primary keys at the database level — no application-level UUID generation needed
- Alembic provides an auditable migration history — every schema change is a timestamped, versioned file committed to the repo
- `pool_pre_ping=True` ensures Celery workers reconnect automatically if Postgres restarts

### Negative
- Two engine configurations (async + sync) adds complexity. **Mitigation:** clearly documented in `app/models/database.py` with comments explaining which context each is used in
- SQLAlchemy 2.0 async requires `expire_on_commit=False` to avoid implicit lazy loading after commit — this is a footgun that new contributors must be aware of. **Mitigation:** documented in `CONTRIBUTING.md`
