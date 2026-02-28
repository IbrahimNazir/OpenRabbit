# ADR-0013: Celery Task Architecture

**Status:** Accepted
**Date:** 2025-03-01
**Context:** Day 3 — Task queue for background PR review processing

## Decision

Use **Celery with Redis** as both broker and result backend, with two dedicated queues:
- `fast_lane` (concurrency=4) — normal PRs (<50 files)
- `slow_lane` (concurrency=1) — large PRs (>50 files)

### Task Design
- Review tasks are **synchronous Celery tasks** that bridge to async code via `asyncio.run()`.
- Each task creates a PRReview DB record, runs the pipeline, updates status.
- Celery workers use the **sync database engine** (psycopg2) per ADR-0006.

### Retry Strategy
- Max 3 retries with exponential backoff: 30s, 120s, 300s.
- Soft time limit: 180s. Hard time limit: 300s.
- On final failure: post error comment on PR, log to dead letter.

### Worker Lifecycle
- `worker_init` signal: initialize sync DB engine.
- `worker_shutdown` signal: dispose engine.

## Consequences
- Two separate worker processes can scale independently.
- Redis handles both message brokering and result storage (simple ops).
- Sync workers avoid Celery's async complexity; pipeline runs async internally.
