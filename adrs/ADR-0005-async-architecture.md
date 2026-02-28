# ADR-0005: Async-First Architecture with Sync Celery Workers

| Field | Value |
|-------|-------|
| **ID** | ADR-0005 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 1 — Project Foundation |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | architecture, async, concurrency, workers |

---

## Context and Problem Statement

OpenRabbit has two fundamentally different types of work:

**Type 1 — I/O-bound, latency-critical:** The API gateway receiving webhooks. Must respond in <100ms. Blocks on nothing (no DB queries, no LLM calls, no file I/O). Pure receive-validate-enqueue.

**Type 2 — Mixed I/O + CPU, latency-tolerant:** The review pipeline. Involves: GitHub API calls (I/O, ~200ms each), LLM API calls (I/O, 5–30 seconds each), Tree-sitter parsing (CPU-bound, ~50ms), Qdrant vector search (I/O, ~100ms). Total pipeline: 30–120 seconds. No user is waiting for an immediate response — GitHub's 200 OK was already sent.

We need an architecture that serves both types efficiently without either blocking the webhook receiver or creating a complex concurrency model that's hard to reason about.

---

## Decision

**Strictly separate the two work types using two different concurrency models:**

### Layer 1: FastAPI + asyncio (I/O-bound, <100ms)
All code in `app/api/` uses async/await exclusively. The FastAPI event loop handles many concurrent webhook requests without blocking. The only operations performed here are: HMAC verification (CPU, <1ms), JSON parsing (CPU, <1ms), Redis enqueue (I/O, ~3ms). Total: under 10ms per request.

### Layer 2: Celery Workers (mixed I/O + CPU, 30–120 seconds)
All code in `app/pipeline/`, `app/rag/`, `app/parsing/` runs in Celery workers. Celery workers are **separate OS processes** — they bypass Python's GIL entirely. Each worker handles one task at a time within its process. We run multiple workers to handle concurrent reviews.

```
Webhook arrives
    │
    ▼
FastAPI (asyncio event loop)
    │  HMAC verify (<1ms)
    │  JSON parse (<1ms)
    │  Redis.lpush() (~3ms) ──────────────▶ Redis Queue
    │                                              │
    ▼                                             │
GitHub gets 200 OK                         Celery Worker pulls task
                                                   │
                                           GitHub API calls (~200ms each)
                                           LLM API calls (~10-30s each)
                                           Tree-sitter parse (~50ms)
                                           Qdrant search (~100ms)
                                                   │
                                           Post PR comments
```

### Why NOT async throughout?

Using `asyncio.gather()` for LLM calls within FastAPI is tempting but wrong for two reasons:

1. **Tree-sitter parsing is CPU-bound** — calling it from an async context blocks the entire event loop. `asyncio.run_in_executor()` can offload it to a thread pool, but this creates subtle concurrency bugs and is harder to test than Celery's simple process model.

2. **Task persistence** — if the API server crashes during a 60-second LLM pipeline, the in-flight review is lost. Celery tasks are persisted in Redis — a crashed worker means another worker picks up the task at the next retry, with the full state intact.

---

## Consequences

### Positive
- Clear architectural boundary: `app/api/` = async, `app/pipeline/` = sync Celery
- Celery workers scale horizontally by adding more worker containers
- CPU-bound Tree-sitter parsing never blocks webhook processing
- Failed reviews automatically retry via Celery's built-in retry mechanism
- Workers can be independently scaled: `docker compose scale worker_fast=4`

### Negative
- Two different concurrency models to understand (asyncio in API, synchronous in workers)
- Data must be serialized to pass from FastAPI to Celery (JSON via Redis). **Mitigation:** keep task arguments minimal — pass IDs, not objects
- Celery workers cannot easily share state with each other. **Mitigation:** all shared state lives in Redis or Postgres, not in worker memory
