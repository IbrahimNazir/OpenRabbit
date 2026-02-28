# ADR-0007: Celery + Redis as Task Queue

| Field | Value |
|-------|-------|
| **ID** | ADR-0007 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 1 — Project Foundation |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | task-queue, celery, redis, workers, async |

---

## Context and Problem Statement

The AI review pipeline takes 30–120 seconds per PR. The FastAPI webhook handler must return HTTP 200 in under 10 seconds. This mandatory decoupling requires a task queue that: accepts a task from the webhook handler in <5ms, durably stores the task until a worker processes it, retries failed tasks with backoff, supports multiple queues with different concurrency settings (fast_lane, slow_lane), and provides visibility into queue depth and worker status.

---

## Decision Drivers

1. **Durability** — If a worker crashes mid-review, the task must be retried automatically (not lost)
2. **Multi-queue routing** — Normal PRs → fast_lane (high concurrency), large PRs → slow_lane (limited concurrency), indexing → index_lane (separate pool)
3. **Retry with backoff** — LLM API rate limits require intelligent retry with exponential backoff
4. **Task visibility** — Flower UI or equivalent to see queue depth, task status, worker health
5. **Operational simplicity** — Must work with a simple `docker compose up` for self-hosters
6. **Python-native** — First-class Python API for defining and calling tasks

---

## Considered Options

### Option A: Celery + Redis (CHOSEN)

Celery is a distributed task queue framework. Redis serves as both the message broker (queue storage) and the result backend (task status/return values).

**Pros:**
- Battle-tested at scale (Airbnb, Robinhood, Instagram have all used Celery in production)
- Redis is already required for our caching layer — no additional infrastructure
- Multi-queue with per-queue concurrency settings: trivial (`-Q fast_lane,slow_lane`)
- Built-in retry with exponential backoff: `@task(max_retries=3, default_retry_delay=60)`
- Flower provides a real-time web UI for monitoring: `docker run mher/flower`
- Dead letter queue support via custom error handlers

**Cons:**
- Celery's async support is partial — `celery[asyncio]` exists but is less mature than sync Celery. We use sync tasks in workers (see ADR-0005).
- Celery 5.x has some configuration quirks (capitalized settings vs old lowercase settings). Mitigated by explicit configuration.

### Option B: Redis Queue (RQ)

A simpler task queue built on Redis.

**Pros:** Simpler than Celery, pure Python, easier to understand
**Cons:** No built-in multi-queue routing, less retry control, smaller community, no Flower equivalent, less documentation for production deployments
**Rejected:** The multi-queue requirement alone eliminates RQ — implementing routing manually would replicate Celery's existing functionality.

### Option C: AWS SQS + Lambda

**Pros:** Fully managed, no operational overhead, auto-scaling
**Cons:** Cloud vendor lock-in; cannot be self-hosted; adds $20–50/month cost; requires AWS credentials; not compatible with our Docker Compose self-host goal
**Rejected:** Self-hostability is a core non-goal violation.

### Option D: Kafka / Redpanda

**Pros:** Replayable events, high throughput, ordered partitioned delivery
**Cons:** Significant operational complexity (Redpanda is simpler but still a separate cluster); for our scale (thousands, not millions of events/day), Kafka is massive overkill; no native Celery integration
**Rejected:** Operational complexity exceeds the benefit for v1. Can revisit if OpenRabbit reaches the scale where Redis becomes a bottleneck.

### Option E: Background threads / asyncio tasks within FastAPI

**Pros:** Zero additional infrastructure
**Cons:** No durability (tasks lost if server restarts), no retry mechanism, CPU-bound tasks block the event loop, no queue depth visibility, no multi-queue routing
**Rejected:** Violates the durability requirement — a server restart loses all in-flight reviews.

---

## Decision

**Use Celery 5.x with Redis as broker and result backend.**

### Queue Configuration

```python
# app/tasks/celery_app.py
from celery import Celery

celery_app = Celery("openrabbit")

celery_app.config_from_object({
    # Broker and backend
    "broker_url": settings.redis_url,
    "result_backend": settings.redis_url,
    
    # Serialization (JSON for debuggability — never pickle)
    "task_serializer": "json",
    "result_serializer": "json",
    "accept_content": ["json"],
    
    # Task routing
    "task_routes": {
        "app.tasks.review_task.run_pr_review": {"queue": "fast_lane"},
        "app.tasks.index_task.index_repository": {"queue": "index_lane"},
        "app.tasks.reply_task.handle_pr_reply": {"queue": "fast_lane"},
    },
    
    # Time limits
    "task_soft_time_limit": 180,    # sends SoftTimeLimitExceeded at 3 min
    "task_time_limit": 300,         # force-kills task at 5 min
    
    # Reliability
    "task_acks_late": True,         # only ACK task after it completes (prevents loss on crash)
    "task_reject_on_worker_lost": True,  # re-queue if worker dies mid-task
    
    # Retry defaults
    "task_max_retries": 3,
    
    # Result expiry
    "result_expires": 86400,        # keep results for 24 hours
    
    # Events for Flower monitoring
    "worker_send_task_events": True,
    "task_send_sent_event": True,
})
```

### Worker Launch Commands

```bash
# Fast lane: 4 concurrent workers for normal PRs
celery -A app.tasks.celery_app worker \
    -Q fast_lane \
    --concurrency=4 \
    --loglevel=info \
    --hostname=fast@%h

# Slow lane: 1 concurrent worker for large PRs (avoids starving other queues)
celery -A app.tasks.celery_app worker \
    -Q slow_lane \
    --concurrency=1 \
    --loglevel=info \
    --hostname=slow@%h

# Index lane: 2 concurrent workers for repo indexing
celery -A app.tasks.celery_app worker \
    -Q index_lane \
    --concurrency=2 \
    --loglevel=info \
    --hostname=index@%h
```

### Task Definition Pattern

```python
# app/tasks/review_task.py
from celery import Task
from app.tasks.celery_app import celery_app

class ReviewTask(Task):
    """Base task class with database session management."""
    abstract = True
    
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Called when all retries are exhausted."""
        # Post error comment to PR
        # Update PRReview.status = 'failed'
        logger.error(f"Review task {task_id} permanently failed: {exc}")

@celery_app.task(
    base=ReviewTask,
    bind=True,
    max_retries=3,
    default_retry_delay=60,    # 1 minute between retries
    autoretry_for=(
        anthropic.RateLimitError,
        httpx.TimeoutException,
        ConnectionError,
    ),
    retry_backoff=True,        # exponential: 60s, 120s, 240s
    retry_backoff_max=300,     # cap at 5 minutes
    retry_jitter=True,         # add randomness to prevent thundering herd
)
def run_pr_review(
    self,
    installation_id: int,
    repo_full_name: str,
    repo_id: int,
    pr_number: int,
    head_sha: str,
    base_sha: str,
) -> dict:
    ...
```

---

## Consequences

### Positive
- `task_acks_late=True` + `task_reject_on_worker_lost=True` guarantees at-least-once delivery — no review is lost if a worker crashes
- Separate queues mean a large PR (50 files, slow lane, 5 minutes of processing) cannot block many small PRs waiting in fast_lane
- `autoretry_for` on `RateLimitError` handles Anthropic API rate limiting automatically — the task sleeps and retries without manual intervention
- JSON serialization makes task arguments inspectable via Redis CLI during debugging

### Negative
- Redis is now a single point of failure — if Redis goes down, no new tasks can be enqueued. **Mitigation:** Redis with persistence (`appendonly yes`) and a health check in Docker Compose
- `task_acks_late=True` can cause duplicate task execution if a worker processes a task successfully but crashes before ACKing. **Mitigation:** idempotency key in Redis prevents double-posting of PR comments
