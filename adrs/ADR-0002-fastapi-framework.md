# ADR-0002: FastAPI as Web Framework

| Field | Value |
|-------|-------|
| **ID** | ADR-0002 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 1 — Project Foundation |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | framework, http, api, async |

---

## Context and Problem Statement

OpenRabbit's API layer has one primary job that is extraordinarily time-sensitive: receive a GitHub webhook POST, validate its HMAC signature, and return HTTP 200 within 10 seconds (GitHub's hard timeout). It also serves secondary endpoints: `/health` for Docker health checks, `/admin/stats` for observability, and potential future OAuth callback flows.

The framework governs:
- How fast the HTTP layer responds (throughput under burst load)
- How cleanly async task dispatch integrates
- How the API contract is documented (important for open-source contributors)
- How request validation is handled
- Developer ergonomics across the 20-day sprint

---

## Decision Drivers

1. **Sub-100ms webhook response** — Framework overhead must be minimal; async is mandatory
2. **Automatic OpenAPI docs** — Critical for open-source contributors to understand the API surface
3. **Pydantic v2 integration** — We use Pydantic for all configuration and data validation throughout the system; tight integration eliminates impedance mismatch
4. **Typing-first design** — Every route handler should have fully typed request/response models
5. **Ecosystem maturity** — Must have stable async DB drivers, middleware, and test utilities
6. **Learning curve** — Must be familiar to the majority of Python backend developers

---

## Considered Options

### Option A: FastAPI + Uvicorn (CHOSEN)
- Async native (ASGI)
- Auto-generates OpenAPI 3.1 docs from type hints
- First-class Pydantic v2 integration
- Dependency injection system built-in
- Background tasks via `BackgroundTasks` (used for lightweight fire-and-forget, not replacing Celery)
- Performance: ~70,000 req/sec on synthetic benchmarks (techempower)

### Option B: Flask + Gunicorn (WSGI)
- Synchronous by default (requires flask-async extension for async routes)
- No built-in OpenAPI generation
- No built-in type validation
- Familiar to most Python developers
- Rejected because: blocking I/O model is fundamentally incompatible with our requirement to dispatch async Celery tasks without blocking. Each webhook would tie up a thread.

### Option C: Django + Django REST Framework
- Full-featured but heavyweight (ORM, admin, auth all included but we don't need them)
- Synchronous by default (Django 4.1+ has async views but ecosystem is mixed)
- Would conflict with our choice of SQLAlchemy as ORM (we need full control over the async session)
- Rejected because: overengineered for a focused webhook handler; DRF serializers add unnecessary complexity when Pydantic v2 does the same job better

### Option D: Starlette (raw, no FastAPI abstraction)
- FastAPI is built on Starlette; using raw Starlette saves ~zero overhead but loses all FastAPI conveniences
- No built-in Pydantic integration, no dependency injection, no auto-docs
- Rejected because: provides no meaningful benefit over FastAPI while removing significant ergonomic value

### Option E: aiohttp
- Pure async HTTP server/client
- No built-in routing beyond basics
- Less typed, no OpenAPI integration
- Rejected because: lower-level than needed and smaller community

---

## Decision

**Use FastAPI 0.115+ with Uvicorn as the ASGI server.**

Uvicorn runs with `--workers 1` in development (single worker, async). In production Docker Compose, we run multiple Uvicorn workers behind the API service (or use Gunicorn as process manager with Uvicorn workers).

### Configuration

```python
# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize DB pool, Redis connection
    await init_db()
    await init_redis()
    yield
    # Shutdown: close connections gracefully
    await close_db()
    await close_redis()

app = FastAPI(
    title="OpenRabbit",
    description="AI-powered GitHub PR reviewer",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",       # Swagger UI
    redoc_url="/redoc",     # ReDoc
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Restrict in production
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

app.include_router(webhook_router, prefix="/api/webhooks")
app.include_router(health_router)
app.include_router(admin_router, prefix="/admin")
```

### Uvicorn production configuration

```python
# Dockerfile CMD
CMD ["uvicorn", "app.main:app", 
     "--host", "0.0.0.0", 
     "--port", "8000",
     "--workers", "2",
     "--loop", "uvloop",      # 2-4x faster than default asyncio loop
     "--http", "httptools"]   # Faster HTTP parsing
```

---

## Consequences

### Positive
- Webhook endpoint responds in <50ms (pure framework overhead negligible; all business logic is async)
- Auto-generated `/docs` (Swagger UI) means contributors can explore the API without reading code
- Pydantic v2 models for request/response validation eliminate entire classes of runtime errors
- FastAPI's dependency injection system (`Depends()`) cleanly passes the database session, Redis client, and config to route handlers without global state
- `BackgroundTasks` allows lightweight fire-and-forget for non-critical post-response work (e.g., updating metrics)

### Negative
- FastAPI is not a full-stack framework — we build our own conventions for error handling, logging middleware, and response envelopes. **Mitigation:** documented in `CONTRIBUTING.md` and enforced via a custom `BaseRouter` class
- Uvicorn + asyncio means CPU-bound code (Tree-sitter parsing) will block the event loop if accidentally called from a route handler. **Mitigation:** all parsing runs in Celery workers, never in FastAPI handlers. Enforced via code review rule: no `import tree_sitter` in `app/api/`

### Neutral
- API server and Celery workers are separate processes — this is by design and creates clean separation of concerns

---

## Route Design Contract

All webhook handlers follow this exact pattern — no exceptions:

```python
@router.post("/github", status_code=200)
async def receive_github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(...),
    x_github_event: str = Header(...),
    config: Settings = Depends(get_settings),
    redis: Redis = Depends(get_redis),
) -> dict:
    # 1. Validate HMAC (fast, <1ms)
    body = await request.body()
    verify_signature(body, x_hub_signature_256, config.webhook_secret)  # raises 403 if invalid
    
    # 2. Parse payload (fast, <1ms)
    payload = json.loads(body)
    
    # 3. Enqueue task (fast, <5ms Redis write)
    run_pr_review.apply_async(args=[...], queue="fast_lane")
    
    # 4. Return immediately
    return {"status": "queued"}
    # Total time: <10ms. GitHub gets its 200 OK instantly.
```

**Rule:** No database queries, no file I/O, no HTTP calls inside webhook route handlers.
