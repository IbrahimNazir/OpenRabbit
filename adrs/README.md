# OpenRabbit — Architecture Decision Records (ADR) Index

Architecture Decision Records capture every significant technical choice made during the build of OpenRabbit. Each ADR is immutable once accepted — if a decision changes, a new ADR supersedes the old one rather than editing in place.

## Format

All ADRs follow the [MADR v3](https://adr.github.io/madr/) (Markdown Architecture Decision Records) format.

## Status Legend

| Status | Meaning |
|--------|---------|
| 🟡 Proposed | Under discussion |
| ✅ Accepted | Decision made, in effect |
| ⛔ Deprecated | No longer valid |
| 🔄 Superseded | Replaced by another ADR |

---

## Days 1–2: Foundation & Integration Layer

| ADR | Title | Status | Day |
|-----|-------|--------|-----|
| [ADR-0001](ADR-0001-python-runtime.md) | Python 3.12 as Primary Runtime | ✅ Accepted | 1 |
| [ADR-0002](ADR-0002-fastapi-framework.md) | FastAPI as Web Framework | ✅ Accepted | 1 |
| [ADR-0003](ADR-0003-github-app-vs-oauth.md) | GitHub App over OAuth App | ✅ Accepted | 1 |
| [ADR-0004](ADR-0004-webhook-hmac-validation.md) | HMAC-SHA256 Webhook Signature Validation | ✅ Accepted | 1 |
| [ADR-0005](ADR-0005-async-first-architecture.md) | Async-First Architecture with Sync Celery Workers | ✅ Accepted | 1 |
| [ADR-0006](ADR-0006-postgresql-sqlalchemy.md) | PostgreSQL + SQLAlchemy 2.0 Async ORM | ✅ Accepted | 1 |
| [ADR-0007](ADR-0007-celery-redis-task-queue.md) | Celery + Redis as Task Queue | ✅ Accepted | 1 |
| [ADR-0008](ADR-0008-docker-compose-dev.md) | Docker Compose for Local Development | ✅ Accepted | 1 |
| [ADR-0009](ADR-0009-poetry-dependency-management.md) | Poetry for Dependency Management | ✅ Accepted | 1 |
| [ADR-0010](ADR-0010-custom-diff-parser.md) | Custom Unified Diff Parser over Third-Party Libraries | ✅ Accepted | 2 |
| [ADR-0011](ADR-0011-gatekeeper-filter-pattern.md) | Pre-LLM Gatekeeper Filter Pattern | ✅ Accepted | 2 |
| [ADR-0012](ADR-0012-installation-token-caching.md) | GitHub Installation Token Caching in Redis | ✅ Accepted | 2 |
