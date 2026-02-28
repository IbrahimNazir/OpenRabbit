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
# ADR-0001: Python 3.12 as Primary Runtime

| Field | Value |
|-------|-------|
| **ID** | ADR-0001 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 1 — Project Foundation |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | runtime, language, tooling |

---

## Context and Problem Statement

OpenRabbit is an AI-native application whose core responsibilities are: handling asynchronous webhook events, integrating with AI APIs (Anthropic Claude), performing code parsing (Tree-sitter), managing vector databases (Qdrant), and orchestrating complex multi-stage LLM pipelines.

We must choose a primary programming language and runtime version that satisfies all of these requirements efficiently and with the broadest available library ecosystem.

The language choice governs:
- Library availability (AI SDKs, AST parsers, vector DB clients)
- Async capability (critical for high-throughput webhook handling)
- Typing support (critical for a maintainable open-source codebase)
- Developer familiarity (open-source contributors)
- Tooling maturity (linters, formatters, test runners)

---

## Decision Drivers

1. **AI ecosystem breadth** — All major AI providers (Anthropic, OpenAI) publish official Python SDKs first
2. **Async-native capability** — Webhook handling must return HTTP 200 in <100ms; processing must be asynchronous
3. **Code parsing libraries** — Tree-sitter has a first-class Python binding (`tree-sitter` 0.20+)
4. **Vector DB support** — Qdrant's Python SDK is the most feature-complete of their language clients
5. **Open-source contributor base** — Python has the largest pool of backend + ML contributors
6. **Type safety** — We need mypy + typing for a maintainable, long-lived open-source project
7. **Runtime performance** — Python 3.12 includes significant performance improvements (up to 15% faster than 3.11 per CPython benchmarks)

---

## Considered Options

| Option | AI SDKs | Async | Tree-sitter | Type System | Contributor Base |
|--------|---------|-------|-------------|-------------|-----------------|
| **Python 3.12** | ✅ Excellent | ✅ asyncio native | ✅ First-class | ✅ mypy + PEP 695 | ✅ Largest |
| Python 3.11 | ✅ Excellent | ✅ asyncio native | ✅ First-class | ✅ mypy | ✅ Largest |
| TypeScript/Node 20 | ✅ Good | ✅ Promises | ⚠️ Partial bindings | ✅ TypeScript | ✅ Large |
| Go 1.22 | ⚠️ Community | ✅ goroutines | ⚠️ CGO bindings | ✅ Native | ⚠️ Medium |
| Rust | ❌ Minimal | ✅ tokio | ⚠️ Via bindings | ✅ Native | ❌ Small (AI) |

---

## Decision

**Use Python 3.12 specifically** (not 3.11 or 3.10).

### Rationale

Python 3.12 introduces two improvements directly relevant to this project:

1. **Per-interpreter GIL (PEP 684)** — While we use Celery workers (not threads) for parallelism, the interpreter improvements benefit asyncio event loop performance.

2. **Better error messages and f-string syntax (PEP 701)** — Directly improves debugging speed during the 20-day sprint.

3. **Type system improvements (PEP 695)** — `type` statement for type aliases makes the codebase cleaner for contributors.

4. **15% performance improvement over 3.11** — Meaningful for a webhook handler processing hundreds of events per minute.

Node/TypeScript was seriously considered because of its strong async model and TypeScript's excellent typing. It was rejected because:
- The Anthropic Python SDK is more feature-complete than the TypeScript SDK (streaming, structured output, tool use)
- Tree-sitter's Python bindings are first-class; the Node bindings require more workarounds
- The ML/AI open-source community contribution base is overwhelmingly Python

---

## Consequences

### Positive
- All Anthropic, OpenAI, and Qdrant SDKs available as first-class packages
- Tree-sitter, NetworkX, tiktoken all have native Python packages with no FFI overhead
- FastAPI, Celery, SQLAlchemy — all Python-native, battle-tested, well-documented
- Largest pool of potential open-source contributors

### Negative
- Python has the GIL — CPU-bound tasks (Tree-sitter parsing of large files) block the event loop. **Mitigation:** all CPU-bound work runs in Celery workers (separate processes), not in the FastAPI async event loop
- Python startup time is slower than Go/Node for cold starts. **Mitigation:** workers are long-running processes, not serverless functions
- Memory usage is higher than Go/Rust. **Mitigation:** acceptable for a self-hosted Docker Compose deployment

### Neutral
- Requires Python 3.12+ in deployment — documented in README and enforced in `pyproject.toml` with `python = "^3.12"`

---

## Implementation Notes

```toml
# pyproject.toml
[tool.poetry.dependencies]
python = "^3.12"
```

```dockerfile
# Dockerfile
FROM python:3.12-slim
```

```yaml
# .github/workflows/ci.yml
- uses: actions/setup-python@v5
  with:
    python-version: "3.12"
```

Enforce at runtime:
```python
# app/main.py
import sys
assert sys.version_info >= (3, 12), "OpenRabbit requires Python 3.12+"
```

---

## Review Date

Revisit when Python 3.13 reaches stable (expected October 2025) — evaluate free-threaded mode (no-GIL) for CPU-bound parsing tasks.
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
# ADR-0003: GitHub App over OAuth App for GitHub Integration

| Field | Value |
|-------|-------|
| **ID** | ADR-0003 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 1 — Project Foundation |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | github, authentication, integration, multi-tenant |

---

## Context and Problem Statement

OpenRabbit must integrate with GitHub to: receive webhook events for PR opens/updates, read repository contents (to fetch diffs and file content), and post review comments back to pull requests. There are three ways to authenticate GitHub API requests as a third-party application.

The choice determines the rate limits we operate under, how we handle multi-tenancy (reviewing PRs across many different organizations), what permissions we can request, and how users install the tool.

---

## Decision Drivers

1. **Multi-tenant by default** — OpenRabbit will review PRs across many independent GitHub organizations and users. Each "tenant" is a GitHub organization or personal account that installs the app.
2. **Fine-grained permissions** — We need Read access to repository contents and Read+Write access to pull requests. We should request only these permissions and nothing more.
3. **Rate limit headroom** — GitHub enforces rate limits per-authenticating-entity. We need high limits to handle burst PR activity.
4. **Installation-scoped tokens** — Security best practice: a token that can only access the specific repositories that an org administrator has approved.
5. **No user login required** — OpenRabbit should work without requiring each repository owner to log in to a web UI. Installation via GitHub Marketplace or direct app install page is sufficient.
6. **Webhook delivery** — The integration must receive webhooks scoped to installed repositories only.

---

## Considered Options

### Option A: GitHub App (CHOSEN)

A GitHub App is a first-class GitHub entity registered once, installed by org admins, and acts as itself (not as any user).

**Authentication mechanism:**
1. App authenticates to GitHub using a JWT signed with its RSA private key (App-level auth, valid 10 minutes)
2. App exchanges the JWT for an Installation Access Token scoped to a specific installation (valid 1 hour)
3. All API calls for a given org/repo use that installation's token

**Rate limits:**
- 15,000 API requests/hour per installation (vs 5,000/hour for OAuth)
- Higher limits for GraphQL

**Permissions:**
- Admin specifies exactly which repositories the app can access at install time
- App requests minimum permission set (Pull Requests: RW, Contents: R, Metadata: R)

**Webhooks:**
- GitHub delivers webhooks directly to the app's configured URL, filtered to installed repositories
- Webhook payload includes `installation.id` — the key for looking up the right access token

### Option B: OAuth App

An OAuth App authenticates as a user who grants it access to their account.

**Authentication mechanism:**
1. User logs in via browser OAuth flow
2. App receives a user-scoped token (access to whatever the user can access)

**Problems for OpenRabbit:**
- Requires a web-based login flow — adds friction, requires a frontend
- Token is scoped to the user who authenticated, not to the organization — if that user leaves the org, the token stops working
- Rate limit: 5,000 req/hour per user (3x lower than GitHub App)
- Cannot filter by repository — if a user grants access, the token can touch all their repos
- Webhook delivery requires the user to manually configure webhooks on each repo

**Rejected because:** OAuth Apps are designed for user-facing applications that act on behalf of a user. OpenRabbit is a bot that acts on behalf of organizations. The OAuth model is fundamentally wrong for our use case.

### Option C: Personal Access Token (PAT)

A PAT is a static token generated by a specific user.

**Problems for OpenRabbit:**
- Cannot be issued programmatically — requires manual generation
- Tied to a single user account — if that account is deleted, all reviews stop
- No multi-tenant support — a single PAT can only serve one account
- Rate limit: 5,000 req/hour shared across all uses of that token
- No webhook integration support

**Rejected because:** PATs are appropriate for personal scripts and CI/CD, not for a multi-tenant SaaS-style tool.

---

## Decision

**Use GitHub App authentication exclusively.**

### GitHub App Configuration

```
App Name: OpenRabbit (or configurable for self-hosters)
Homepage URL: https://github.com/your-org/openrabbit
Webhook URL: https://your-domain.com/api/webhooks/github
Webhook Secret: [generated with openssl rand -hex 32]

Permissions:
  Pull requests: Read & Write    # post comments, read PR data
  Contents: Read                 # fetch file content at specific SHAs
  Metadata: Read                 # required for all apps
  Commit statuses: Read & Write  # post pending/success status

Events Subscribed:
  - pull_request                 # opened, synchronize, reopened, closed
  - pull_request_review_comment  # created (for "fix this" replies)
  - installation                 # created, deleted (tenant onboarding)
```

### Authentication Flow

```
┌─────────────┐     1. JWT (App-level)    ┌─────────────────┐
│  OpenRabbit │ ─────────────────────────▶│   GitHub API    │
│             │◀─────────────────────────  │                 │
│             │  2. Installation Token     │                 │
│             │                           │                 │
│             │  3. API calls with token   │                 │
│             │ ─────────────────────────▶│                 │
└─────────────┘                           └─────────────────┘
```

### JWT Generation (Python)

```python
import time
import jwt  # PyJWT library

def generate_app_jwt(app_id: str, private_key_pem: str) -> str:
    """
    Generate a short-lived JWT for GitHub App authentication.
    Valid for 10 minutes (GitHub's maximum).
    """
    now = int(time.time())
    payload = {
        "iat": now - 60,    # issued-at (60s in the past to account for clock drift)
        "exp": now + 540,   # expires in 9 minutes (GitHub max is 10)
        "iss": app_id,      # issuer = GitHub App ID
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")
```

### Installation Token Exchange

```python
async def get_installation_token(installation_id: int) -> str:
    """Exchange App JWT for installation-scoped access token."""
    jwt_token = generate_app_jwt(settings.github_app_id, settings.private_key)
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        response.raise_for_status()
        data = response.json()
        return data["token"]  # expires at data["expires_at"] (1 hour from now)
```

---

## Consequences

### Positive
- Each installation (organization or user) gets its own installation token — tokens are naturally scoped to the correct repositories
- 15,000 API requests/hour per installation means even high-volume engineering orgs won't hit limits under normal PR activity (a typical org makes <500 GitHub API calls/hour)
- Admins see exactly which repositories OpenRabbit can access and can revoke at any time via GitHub's UI
- No user login UI required — the installation flow is entirely handled by GitHub's standard app installation page
- `installation.id` in every webhook payload gives us the tenant key with zero lookup required

### Negative
- Private key (`.pem` file) must be stored securely and never committed to git. **Mitigation:** documented in README, enforced via `.gitignore`, checked in CI via `git-secrets`
- App-level JWT must be regenerated every 10 minutes — adds a code path for token management. **Mitigation:** solved by ADR-0012 (Redis-based token caching)
- Self-hosted deployments require the user to create their own GitHub App — adds ~30 minutes of setup. **Mitigation:** `scripts/setup.sh` walks through this interactively

### Neutral
- The `installation_id` is the primary tenant identifier throughout the entire codebase — every database record, every Celery task argument, every Redis key includes it

---

## Security Considerations

1. **Private key storage:** Store as a `.pem` file referenced by path in `.env`. Never store in the database. Never include in Docker images — mount as a volume secret.
2. **Webhook secret:** Separate from the private key. Used for HMAC validation only (see ADR-0004).
3. **Token rotation:** Installation tokens expire after 1 hour. Our caching strategy (ADR-0012) ensures we never use an expired token.
4. **Minimum permissions:** Request only the permissions listed above. Never request `Administration`, `Members`, or any write access beyond pull requests.
# ADR-0004: HMAC-SHA256 Webhook Signature Validation

| Field | Value |
|-------|-------|
| **ID** | ADR-0004 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 1 — Project Foundation |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | security, webhooks, authentication, hmac |

---

## Context and Problem Statement

OpenRabbit exposes a public HTTP endpoint (`POST /api/webhooks/github`) that must accept webhook payloads from GitHub. Because this endpoint is public, it is reachable by anyone on the internet — not just GitHub. Without verification, an attacker could:

1. Send fake webhook payloads to trigger AI reviews on arbitrary code (costing us LLM API money)
2. Replay captured webhook payloads to re-trigger reviews
3. Inject crafted payloads that manipulate our pipeline logic
4. Enumerate internal repository data by observing our responses

We need a mechanism to cryptographically verify that every incoming webhook payload was genuinely sent by GitHub and has not been tampered with in transit.

---

## Decision Drivers

1. **Cryptographic certainty** — Verification must be mathematically sound, not security-through-obscurity
2. **Latency** — Verification must complete in <1ms (before any other processing)
3. **Constant-time comparison** — Prevent timing attacks that could allow secret enumeration
4. **GitHub standard compliance** — Must work with the standard GitHub webhook signature mechanism
5. **Simplicity** — Every contributor must be able to understand the verification code in under 2 minutes

---

## Considered Options

### Option A: HMAC-SHA256 Signature Verification (CHOSEN)

GitHub computes `HMAC-SHA256(secret, payload_body)` and sends it in the `X-Hub-Signature-256` header as `sha256={hex_digest}`. We compute the same HMAC with our shared secret and compare.

**Security properties:**
- Requires knowledge of the shared secret to forge
- The payload body is part of the signature — any tampering with the body invalidates the signature
- Replay attacks are possible in theory but limited by GitHub's delivery retry window (~1 hour); our idempotency key (see `review_task.py`) handles duplicates

### Option B: IP Allowlist Only

Verify that the request originates from GitHub's published webhook IP ranges.

**Problems:**
- GitHub's IP ranges change over time — requires continuous updates
- Provides no protection if an attacker controls a machine in GitHub's IP range (e.g., GitHub Actions runner abuse)
- Does not protect against payload tampering
- Rejected: insufficient security, high maintenance burden

### Option C: Request Signing with Asymmetric Keys (RSA/Ed25519)

Use public/private key signing where GitHub signs with a private key and we verify with their public key.

**Problems:**
- GitHub does not support this mechanism for webhooks (as of 2025) — HMAC is their specified standard
- Would require us to implement a custom non-standard signature scheme
- Rejected: not supported by GitHub's webhook infrastructure

### Option D: API Key in URL/Header

Include a secret token in the webhook URL (`/api/webhooks/github?token=xxx`) or as a custom header.

**Problems:**
- URL-based secrets appear in server logs and proxy logs
- No protection against payload tampering
- Rejected: weaker than HMAC and exposes the secret in logs

---

## Decision

**Implement HMAC-SHA256 signature verification as the first operation in every webhook handler, before any other processing.**

The verification must happen before: JSON parsing, database queries, task enqueueing, or any other work. A request that fails HMAC verification is dropped immediately with `HTTP 403`.

### Implementation

```python
# app/core/security.py
import hashlib
import hmac
from fastapi import HTTPException, Request

async def verify_github_signature(
    request: Request,
    body: bytes,
    secret: str,
) -> None:
    """
    Verify HMAC-SHA256 signature from GitHub webhook.
    
    GitHub sends: X-Hub-Signature-256: sha256=<hex_digest>
    We compute:   sha256=HMAC-SHA256(secret, body)
    
    Uses hmac.compare_digest() for constant-time comparison
    to prevent timing attacks.
    
    Raises:
        HTTPException(403): if signature is missing or invalid
    """
    signature_header = request.headers.get("X-Hub-Signature-256")
    
    if not signature_header:
        raise HTTPException(
            status_code=403,
            detail="Missing X-Hub-Signature-256 header"
        )
    
    if not signature_header.startswith("sha256="):
        raise HTTPException(
            status_code=403,
            detail="Invalid signature format — expected sha256= prefix"
        )
    
    received_signature = signature_header[7:]  # strip "sha256=" prefix
    
    # Compute expected HMAC
    expected_signature = hmac.new(
        key=secret.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    
    # CRITICAL: Use constant-time comparison to prevent timing attacks.
    # A naive string comparison (==) leaks information about how many
    # characters match, allowing an attacker to enumerate the secret
    # one character at a time via timing measurements.
    if not hmac.compare_digest(expected_signature, received_signature):
        raise HTTPException(
            status_code=403,
            detail="Invalid webhook signature"
        )
    # If we reach here, the signature is valid — proceed with processing
```

### Webhook Handler Integration

```python
# app/api/webhooks.py
@router.post("/github", status_code=200)
async def receive_github_webhook(
    request: Request,
    config: Settings = Depends(get_settings),
) -> dict:
    # Step 1: Read raw body BEFORE parsing
    # We must read the raw bytes for HMAC — parsing first would discard them
    body = await request.body()
    
    # Step 2: Verify signature — FIRST OPERATION, NO EXCEPTIONS
    await verify_github_signature(request, body, config.github_webhook_secret)
    
    # Step 3: Only now parse the payload
    payload = json.loads(body)
    
    # Step 4: Process (enqueue task) and return 200
    # ...
    return {"status": "accepted"}
```

### Secret Generation

```bash
# Generate a cryptographically strong webhook secret
openssl rand -hex 32
# Output example: a3f8b2c1d4e5f6789012345678901234567890abcdef1234567890abcdef12

# Store in .env
GITHUB_WEBHOOK_SECRET=a3f8b2c1d4e5f6789012345678901234567890abcdef1234567890abcdef12
```

---

## Consequences

### Positive
- Every webhook request is cryptographically verified before any processing occurs
- Fake payloads are rejected in <1ms with zero resource cost (no DB, no LLM, no task queue touched)
- Constant-time comparison prevents timing attacks — the comparison time is independent of how similar the provided signature is to the correct one
- Compatible with GitHub's standard webhook mechanism — no custom configuration needed on the GitHub side
- The verification function has zero external dependencies (only Python stdlib `hmac` and `hashlib`)

### Negative
- The shared secret must be securely stored and rotated if compromised. **Mitigation:** stored only in `.env` (never in code or DB), documented in `SECURITY.md` with rotation instructions
- Replay attacks are theoretically possible within GitHub's retry window. **Mitigation:** our task idempotency key (`review:{repo_id}:{pr_number}:{head_sha}` in Redis) prevents double-processing of identical events

### Neutral
- The `request.body()` must be called once before JSON parsing — FastAPI's request body is a stream and can only be read once. This is why we read raw bytes first, verify, then parse.

---

## Testing Requirements

The following test cases are mandatory for this ADR (see `tests/test_webhooks.py`):

```python
def test_missing_signature_returns_403():
    """No X-Hub-Signature-256 header → 403"""

def test_malformed_signature_returns_403():
    """Header exists but doesn't start with 'sha256=' → 403"""

def test_wrong_signature_returns_403():
    """Correct format, wrong HMAC value → 403"""

def test_valid_signature_returns_200():
    """Correct HMAC with test secret → 200"""

def test_tampered_body_returns_403():
    """Valid signature for original body, body modified → 403"""

def test_timing_consistency():
    """
    Correct and incorrect signatures should take approximately the same
    time to verify (within 10ms of each other across 100 iterations).
    This validates the constant-time comparison property.
    """
```

---

## Secrets Rotation Procedure

If the webhook secret is compromised:

1. Generate a new secret: `openssl rand -hex 32`
2. Update the secret in GitHub App settings (Settings → Webhooks → Edit)
3. Update `.env` on all running instances
4. Restart the API gateway service
5. **Do NOT** update secret in GitHub before updating the application — there is a brief window where GitHub sends events with the new secret that the old application rejects. Use a rolling update to minimize impact.
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
# ADR-0008: Docker Compose for Local Development

| Field | Value |
|-------|-------|
| **ID** | ADR-0008 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 1 — Project Foundation |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | devex, docker, infrastructure, local-dev |

---

## Context and Problem Statement

OpenRabbit depends on four external services: PostgreSQL, Redis, Qdrant, and (for local webhook testing) a smee.io relay client. Every developer who wants to contribute — and every user who wants to self-host — needs all four services running correctly with the right versions and configurations.

Without a standardized approach, contributors spend hours debugging "works on my machine" issues: wrong PostgreSQL version, Redis not started, Qdrant port conflict.

---

## Decision

**Use Docker Compose as the single, canonical way to run all infrastructure services, for both development and production self-hosting.**

The application code (FastAPI + Celery workers) runs natively on the host during development for fast iteration, while all stateful services run in Docker.

### docker-compose.yml

```yaml
version: "3.9"
name: openrabbit

services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: openrabbit
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-devpassword}
      POSTGRES_DB: openrabbit
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U openrabbit"]
      interval: 5s
      timeout: 5s
      retries: 10
    networks:
      - openrabbit_net

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: >
      redis-server
      --appendonly yes
      --appendfsync everysec
      --maxmemory 512mb
      --maxmemory-policy allkeys-lru
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10
    networks:
      - openrabbit_net

  qdrant:
    image: qdrant/qdrant:latest
    restart: unless-stopped
    ports:
      - "6333:6333"   # REST API
      - "6334:6334"   # gRPC
    volumes:
      - qdrant_data:/qdrant/storage
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6333/health"]
      interval: 10s
      timeout: 5s
      retries: 10
    networks:
      - openrabbit_net

  smee:
    image: node:18-alpine
    restart: unless-stopped
    command: >
      sh -c "npm install -g smee-client &&
             smee --url ${SMEE_URL} --target http://host.docker.internal:8000/api/webhooks/github"
    extra_hosts:
      - "host.docker.internal:host-gateway"   # Linux compatibility
    profiles:
      - dev     # Only started with: docker compose --profile dev up
    networks:
      - openrabbit_net

  # Production application services (commented out for dev — run natively)
  # Uncomment for full Docker deployment
  # api:
  #   build: .
  #   ...

volumes:
  postgres_data:
  redis_data:
  qdrant_data:

networks:
  openrabbit_net:
    driver: bridge
```

### Why Compose over alternatives?

| Option | Zero-install overhead | Reproducible | Self-host friendly | Learning curve |
|--------|----------------------|--------------|-------------------|----------------|
| **Docker Compose** | ✅ (Docker Desktop) | ✅ | ✅ | Low |
| Kubernetes (local) | ❌ (minikube/kind) | ✅ | ❌ Complex | High |
| Manual installation | ❌ Per-service setup | ❌ | ⚠️ | Low per service |
| Nix/Devcontainer | ❌ Nix install | ✅ | ❌ | High |

### `--profile dev` pattern

The smee relay client only needs to run during local development (where the API is on localhost). By using `profiles: [dev]`, `docker compose up` (without profiles) starts only postgres/redis/qdrant. `docker compose --profile dev up` adds smee for local webhook testing.

---

## Consequences

### Positive
- `docker compose up -d` gets any contributor to a working state in under 2 minutes
- `postgres:16-alpine` and `redis:7-alpine` use Alpine base images — small, fast to pull
- Persistent volumes mean data survives `docker compose restart`
- Health checks ensure dependent services start only after dependencies are ready

### Negative
- Docker Desktop required on macOS/Windows (free for individual use; paid for large companies). **Mitigation:** documented in README with alternatives (Colima, Podman)
- `host.docker.internal` works differently on Linux vs macOS. **Mitigation:** `extra_hosts: ["host.docker.internal:host-gateway"]` in the smee service handles Linux
# ADR-0009: Poetry for Dependency Management

| Field | Value |
|-------|-------|
| **ID** | ADR-0009 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 1 — Project Foundation |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | tooling, dependencies, python, packaging |

---

## Context and Problem Statement

OpenRabbit has ~25 production dependencies and ~10 development dependencies. Dependency management for an open-source Python project must: lock exact versions for reproducibility, separate runtime from dev dependencies, and produce a standard-format lock file that contributors can trust.

---

## Decision

**Use Poetry 1.8+ for dependency management and packaging.**

```toml
# pyproject.toml
[tool.poetry]
name = "openrabbit"
version = "0.1.0"
description = "AI-powered GitHub PR reviewer"
license = "MIT"
python = "^3.12"

[tool.poetry.dependencies]
fastapi = "^0.115"
uvicorn = {extras = ["standard"], version = "^0.30"}
celery = {extras = ["redis"], version = "^5.4"}
redis = "^5.0"
sqlalchemy = {extras = ["asyncio"], version = "^2.0"}
asyncpg = "^0.29"
alembic = "^1.13"
anthropic = "^0.40"
PyGithub = "^2.4"
httpx = "^0.27"
pydantic-settings = "^2.5"
qdrant-client = "^1.12"
tree-sitter = "^0.23"
networkx = "^3.4"
tiktoken = "^0.8"
structlog = "^24.4"
python-jose = {extras = ["cryptography"], version = "^3.3"}

[tool.poetry.dev-dependencies]
pytest = "^8.3"
pytest-asyncio = "^0.24"
pytest-mock = "^3.14"
httpx = "^0.27"   # for TestClient
ruff = "^0.7"
mypy = "^1.11"
black = "^24.10"

[tool.ruff]
line-length = 100
select = ["E", "F", "I", "N", "W", "UP"]  # Enable: pyflakes, isort, pep8-naming, pyupgrade

[tool.mypy]
python_version = "3.12"
strict = true
ignore_missing_imports = true
```

**Why Poetry over pip + requirements.txt?**
- `poetry.lock` provides deterministic installs — exact same versions on every machine, every CI run
- `poetry add` resolves transitive dependencies automatically and checks for conflicts
- Separates `[tool.poetry.dependencies]` from `[tool.poetry.dev-dependencies]` cleanly
- Standard `pyproject.toml` format (PEP 517/518) means the project is pip-installable too
- `poetry build` produces distributable wheel — important for future PyPI publication

**Why not pip-tools?** pip-tools (pip-compile) is excellent but requires maintaining `requirements.in` separately from `pyproject.toml`. Poetry unifies both in one file.
# ADR-0010: Custom Unified Diff Parser over Third-Party Libraries

| Field | Value |
|-------|-------|
| **ID** | ADR-0010 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 2 — GitHub Client & Diff Fetching |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | diff, parsing, core, github-api |

---

## Context and Problem Statement

Every PR review begins by parsing GitHub's unified diff format into structured data. The parser must produce: file-level metadata (filename, status, language), hunk-level data (line ranges, added/removed/context lines), and — critically — the **diff position** for every line.

The diff position is GitHub's 1-indexed line counter within the entire diff for a file, starting from the first hunk header. It is not the same as the line number in the new file. Getting this wrong means every inline PR comment is posted at the wrong location (GitHub's API returns 422 Unprocessable Entity for invalid positions).

---

## Decision Drivers

1. **Diff position accuracy** — Must produce exactly the position value that GitHub's Review API expects. This is OpenRabbit's most critical correctness requirement for the core user experience.
2. **Function context extraction** — Git diffs sometimes include a function name after `@@` (e.g., `@@ -10,5 +10,7 @@ def process_payment():`). We need this for enriching hunk context.
3. **Edge case handling** — Binary files, renamed files, files with no newline at end, empty diffs, added/deleted files (which have no "old" path).
4. **Zero external dependency** — The diff parser is our most foundational component. External library bugs directly block all reviews.
5. **Testability** — Must be testable in isolation with a comprehensive fixture library.

---

## Considered Options

### Option A: Custom Parser (CHOSEN)

Write our own parser of ~150 lines. Parse the unified diff format line by line, maintaining a `diff_position` counter that increments for every line that is not a file header.

**Pros:** Full control over position calculation, function context extraction, error handling
**Cons:** Must be written and tested thoroughly

### Option B: `unidiff` library (PyPI)

```python
from unidiff import PatchSet
patch = PatchSet(diff_text)
```

**Problems:**
- `unidiff` does not expose the raw `diff_position` as GitHub defines it — it exposes `source_line_no`, `target_line_no`, `diff_line_no` but the semantics differ from what GitHub's API requires
- The library maps positions differently for multi-hunk files — our internal testing found position off-by-one errors when a diff has multiple hunks per file
- No function context extraction from the `@@` header
- Adding a dependency for something we can write in 150 lines adds a version maintenance burden and a potential security surface
- **Rejected:** position calculation semantics are wrong for GitHub's API

### Option C: `whatthepatch` library

Similar to `unidiff` — provides parsed hunks but not GitHub-compatible positions. Rejected for the same reason.

### Option D: Use GitHub's API to get file changes directly

Instead of parsing the diff text, call `GET /repos/{owner}/{repo}/pulls/{pr}/files` to get structured file change data including `changes`, `additions`, `deletions` per file.

**Problems:**
- This API returns file-level metadata but not hunk-level data — we lose the ability to do hunk-level analysis (which hunks were changed, in which function)
- The `patch` field in the response is the same unified diff format, so we'd still need to parse it
- No position data — the GitHub Pulls Files API doesn't return the diff position either; only the raw patch text
- **Rejected:** doesn't solve the problem and adds an extra API call

---

## Decision

**Implement a custom unified diff parser in `app/core/diff_parser.py`.**

### Diff Position Calculation (Critical)

The diff position follows these rules — this is the exact algorithm GitHub uses:

```python
def compute_diff_positions(diff_text: str) -> dict[str, dict[int, int]]:
    """
    Returns: { filename: { new_file_line_number: diff_position } }
    
    diff_position rules:
    - Reset to 0 for each new file (diff --git a/... b/...)
    - The @@ hunk header line itself counts as position 1 for that hunk
    - Each subsequent line (context, added, removed) increments position by 1
    - Removed lines (-) increment position but have no new_lineno mapping
    - Context lines and added lines (+) both increment position
    - Only added lines and context lines can have PR comments (not removed lines)
    
    CRITICAL: diff_position does NOT reset between hunks — it is cumulative
    within the entire file diff.
    """
    positions = {}
    current_file = None
    diff_pos = 0
    new_line = 0
    
    for line in diff_text.split('\n'):
        if line.startswith('diff --git'):
            # New file — extract filename, reset position counter
            current_file = extract_filename(line)
            positions[current_file] = {}
            diff_pos = 0
            
        elif line.startswith('@@') and current_file:
            # Hunk header — increment position, update new_line counter
            diff_pos += 1
            match = re.search(r'\+(\d+)(?:,\d+)?', line)
            new_line = int(match.group(1)) - 1  # will be incremented on first line
            
        elif current_file and diff_pos > 0:
            diff_pos += 1
            if line.startswith('+'):
                new_line += 1
                positions[current_file][new_line] = diff_pos
            elif line.startswith('-'):
                pass  # removed lines don't increment new_line
            else:  # context line (starts with ' ')
                new_line += 1
                positions[current_file][new_line] = diff_pos
    
    return positions
```

### Complete Data Model

```python
@dataclass
class DiffLine:
    content: str
    line_type: Literal['added', 'removed', 'context']
    old_lineno: int | None   # None for added lines
    new_lineno: int | None   # None for removed lines
    diff_position: int       # GitHub's 1-indexed position within the file diff

@dataclass
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str              # Raw @@ line including optional function context
    function_context: str | None  # e.g., "def process_payment():" extracted from header
    lines: list[DiffLine]

@dataclass
class FileDiff:
    filename: str            # New filename (after rename if applicable)
    old_filename: str | None # Old filename before rename, None if not renamed
    status: Literal['added', 'modified', 'removed', 'renamed']
    language: str | None     # Detected from extension
    hunks: list[DiffHunk]
    additions: int           # Total lines added
    deletions: int           # Total lines removed
    is_binary: bool = False  # True for binary files — skip review

# Derived utility function — the most critical function for comment posting
def build_line_to_position_map(file_diff: FileDiff) -> dict[int, int]:
    """
    Returns { new_file_line_number: diff_position } for all commentable lines.
    Only added (+) and context lines are commentable — removed lines are not.
    """
    return {
        line.new_lineno: line.diff_position
        for hunk in file_diff.hunks
        for line in hunk.lines
        if line.line_type in ('added', 'context') and line.new_lineno is not None
    }
```

### Language Detection

```python
EXTENSION_TO_LANGUAGE = {
    '.py': 'python',
    '.js': 'javascript',
    '.jsx': 'javascript',
    '.ts': 'typescript',
    '.tsx': 'typescript',
    '.go': 'go',
    '.rs': 'rust',
    '.java': 'java',
    '.kt': 'kotlin',
    '.swift': 'swift',
    '.rb': 'ruby',
    '.php': 'php',
    '.cs': 'csharp',
    '.cpp': 'cpp',
    '.c': 'c',
    '.h': 'c',
    '.sh': 'bash',
    '.sql': 'sql',
    '.yaml': 'yaml',
    '.yml': 'yaml',
    '.json': 'json',
    '.tf': 'terraform',
    '.proto': 'protobuf',
}
```

---

## Consequences

### Positive
- Complete control over position calculation — we own the algorithm
- Can add function context extraction with zero library changes
- No external dependency that could change its behavior between versions
- Parser is a pure function: `parse_diff(str) -> list[FileDiff]` — trivially testable

### Negative
- We own the bug surface. **Mitigation:** 10+ test fixtures covering all edge cases (new file, deleted file, rename, binary, multi-hunk, no-newline-at-end, empty diff), run in CI on every commit

### Mandatory Test Fixtures

```
tests/fixtures/diffs/
├── simple_modification.diff      # 1 file, 1 hunk, basic add/remove
├── new_file.diff                 # added file (no old path, all lines are additions)
├── deleted_file.diff             # removed file (all lines are deletions)
├── renamed_file.diff             # rename with modifications
├── multi_hunk.diff               # 1 file, 3 hunks far apart
├── adjacent_hunks.diff           # 2 hunks within 5 lines of each other
├── multi_file.diff               # 4 files changed in one diff
├── binary_file.diff              # binary file (should be skipped)
├── no_newline.diff               # "\ No newline at end of file" marker
├── function_context.diff         # @@ header includes function name
└── empty_diff.diff               # PR with no changes (edge case)
```
# ADR-0011: Pre-LLM Gatekeeper Filter Pattern

| Field | Value |
|-------|-------|
| **ID** | ADR-0011 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 2 — GitHub Client & Diff Fetching |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | cost-control, filtering, architecture, gatekeeper |

---

## Context and Problem Statement

In production, approximately 40–65% of all incoming webhook events should not trigger an LLM review:

- ~30–40% are automated bot PRs (Dependabot, Renovate, Snyk) that update dependencies — no human code was written
- ~15–20% are documentation-only PRs (`.md`, `.rst`, `.txt` files only) — no code to review
- ~5% are lockfile-only PRs (`package-lock.json`, `yarn.lock`) — generated files, nothing reviewable
- ~5% are draft PRs — the developer has explicitly signaled the code is not ready for review

Running these through the LLM pipeline wastes money (LLM API costs) and time (adds review noise that developers ignore, training them to ignore AI reviews entirely).

---

## Decision

**Implement a Gatekeeper Filter as the first step after receiving a validated webhook, before any database writes or task enqueueing.**

The Gatekeeper is a cheap, deterministic, rule-based decision engine. It has exactly one job: decide whether this webhook event should proceed to the review pipeline, and if so, which queue.

### Filter Rules (Applied in Order)

```python
@dataclass
class FilterResult:
    should_process: bool
    reason: str                                      # Human-readable reason for the decision
    queue: Literal['fast_lane', 'slow_lane', 'skip'] # Where to route

BOT_LOGINS = frozenset({
    'dependabot[bot]',
    'dependabot-preview[bot]',
    'renovate[bot]',
    'snyk-bot',
    'github-actions[bot]',
    'imgbot[bot]',
    'whitesource-bolt-for-github[bot]',
    'semantic-release-bot',
    'allcontributors[bot]',
})

NO_REVIEW_PATTERNS = (
    # Documentation
    '*.md', '*.rst', '*.txt', '*.adoc', '*.wiki',
    # Images and media
    '*.png', '*.jpg', '*.jpeg', '*.gif', '*.svg', '*.ico', '*.webp',
    # Lockfiles (generated, never hand-written)
    'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
    '*.lock', '*.sum', 'Cargo.lock', 'poetry.lock', 'Gemfile.lock',
    'composer.lock', 'packages.lock.json',
    # Build artifacts
    '*.min.js', '*.min.css', '*.map',
    # IDE and config
    '.gitignore', '.gitattributes', '.editorconfig',
    '*.iml',  # IntelliJ module files
)

LARGE_PR_THRESHOLD = 50  # files changed

class FilterEngine:
    def should_review(self, payload: dict, changed_files: list[str]) -> FilterResult:
        
        # Rule 1: Bot author
        author = payload.get('pull_request', {}).get('user', {}).get('login', '')
        if author in BOT_LOGINS or author.endswith('[bot]'):
            return FilterResult(False, f"Bot PR from {author}", 'skip')
        
        # Rule 2: Label override
        labels = [l['name'] for l in payload.get('pull_request', {}).get('labels', [])]
        if 'skip-ai-review' in labels:
            return FilterResult(False, "skip-ai-review label present", 'skip')
        
        # Rule 3: Draft PR
        if payload.get('pull_request', {}).get('draft', False):
            return FilterResult(False, "Draft PR — awaiting ready-for-review", 'skip')
        
        # Rule 4: All files are no-review patterns
        reviewable = self.get_reviewable_files(changed_files)
        if not reviewable:
            return FilterResult(False, f"All {len(changed_files)} files match no-review patterns", 'skip')
        
        # Rule 5: Large PR → slow lane
        if len(changed_files) > LARGE_PR_THRESHOLD:
            return FilterResult(True, f"Large PR: {len(changed_files)} files → slow lane", 'slow_lane')
        
        # Default: proceed with fast lane
        return FilterResult(True, f"Reviewable PR: {len(reviewable)} code files", 'fast_lane')
```

### Why NOT filter inside the Celery worker?

Filtering inside the worker would: (a) consume a worker slot for the 60% of events that should be skipped, (b) still require a DB write for the PRReview record, (c) add latency before the skip decision. The Gatekeeper runs before any of this work, in the API gateway itself.

### Future Extension: Security Scan Override

The Gatekeeper pattern enables a future feature: even bot PRs get a lightweight security scan (checking for supply chain attacks, malicious dependency substitutions). This is implemented as a separate task queue (`security_scan`) that the Gatekeeper can route bot PRs to instead of silently dropping them.

---

## Consequences

### Positive
- 40–65% reduction in LLM API costs (the largest cost driver) — visible immediately in cost tracking
- Workers are available for actual review tasks, not wasted on bot/doc PRs
- Rule-based filtering is deterministic and testable — every filter decision is logged with its reason

### Negative
- Bot PRs are silently skipped — if a bot introduces a security vulnerability (e.g., a malicious package in a compromised Dependabot PR), we won't catch it. **Mitigation:** documented as a known limitation; security scan feature in backlog
# ADR-0012: GitHub Installation Token Caching in Redis

| Field | Value |
|-------|-------|
| **ID** | ADR-0012 |
| **Status** | ✅ Accepted |
| **Deciders** | Core Team |
| **Date** | Day 2 — GitHub Client & Diff Fetching |
| **Sprint Phase** | Phase 1: MVP |
| **Tags** | caching, github, authentication, redis, performance |

---

## Context and Problem Statement

Every GitHub API call requires an Installation Access Token scoped to the specific GitHub App installation (organization or user). These tokens are obtained by: generating a short-lived App JWT (valid 10 minutes), exchanging the JWT for an Installation Token at `POST /app/installations/{id}/access_tokens` (valid 60 minutes).

Without caching, each of the ~10 GitHub API calls per PR review would trigger a new token exchange — 10 × 200ms = 2 extra seconds per review just for token overhead, plus burning 10 of the 5,000 rate-limited token exchange requests per hour.

---

## Decision

**Cache installation tokens in Redis with a TTL of 55 minutes (5 minutes short of the 60-minute expiry).**

The 5-minute buffer ensures we never use a token that is about to expire or has just expired due to clock skew between our server and GitHub's servers.

### Cache Key Structure

```
github:token:{installation_id}
```

Examples:
```
github:token:12345678    → "ghs_abc123def456..."  (TTL: 3180s remaining)
github:token:87654321    → "ghs_xyz789..."         (TTL: 1200s remaining)
```

### Implementation

```python
# app/core/github_client.py
class GitHubClient:
    CACHE_KEY_PREFIX = "github:token:"
    TOKEN_TTL_SECONDS = 55 * 60  # 55 minutes (token valid for 60, buffer 5)
    
    def __init__(self, installation_id: int, redis: Redis):
        self.installation_id = installation_id
        self.redis = redis
        self._token: str | None = None
    
    async def get_access_token(self) -> str:
        """Get a valid installation token, using cache when available."""
        cache_key = f"{self.CACHE_KEY_PREFIX}{self.installation_id}"
        
        # Try cache first
        cached = await self.redis.get(cache_key)
        if cached:
            return cached.decode("utf-8")
        
        # Cache miss — generate fresh token
        token = await self._fetch_fresh_token()
        
        # Cache with 55-minute TTL
        await self.redis.setex(cache_key, self.TOKEN_TTL_SECONDS, token)
        
        return token
    
    async def _fetch_fresh_token(self) -> str:
        """Exchange App JWT for installation access token."""
        app_jwt = self._generate_app_jwt()
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"https://api.github.com/app/installations/{self.installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                }
            )
            
            if response.status_code == 401:
                raise GitHubAuthError("App JWT is invalid — check GITHUB_APP_PRIVATE_KEY_PATH")
            if response.status_code == 404:
                raise GitHubInstallationNotFoundError(
                    f"Installation {self.installation_id} not found — may have been uninstalled"
                )
            response.raise_for_status()
            
            data = response.json()
            return data["token"]
    
    async def _invalidate_token(self) -> None:
        """Force token refresh on next request (e.g., after 403 from GitHub API)."""
        cache_key = f"{self.CACHE_KEY_PREFIX}{self.installation_id}"
        await self.redis.delete(cache_key)
    
    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Make an authenticated GitHub API request with automatic token refresh."""
        token = await self.get_access_token()
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method, url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    **kwargs.pop("headers", {}),
                },
                **kwargs
            )
            
            if response.status_code == 403:
                # Token may be revoked — invalidate cache and retry once
                remaining = response.headers.get("X-RateLimit-Remaining", "unknown")
                if remaining == "0":
                    raise GitHubRateLimitError(
                        f"Rate limit exceeded. Resets at: {response.headers.get('X-RateLimit-Reset')}"
                    )
                # Otherwise, token was likely revoked — invalidate and retry
                await self._invalidate_token()
                token = await self.get_access_token()
                response = await client.request(method, url, **kwargs)
            
            return response
```

### Rate Limit Monitoring

```python
async def check_rate_limit(self, response: httpx.Response) -> None:
    """Log rate limit status from every GitHub API response."""
    remaining = int(response.headers.get("X-RateLimit-Remaining", -1))
    limit = int(response.headers.get("X-RateLimit-Limit", -1))
    reset_ts = int(response.headers.get("X-RateLimit-Reset", 0))
    
    if remaining < 100:
        logger.warning(
            "GitHub rate limit low",
            installation_id=self.installation_id,
            remaining=remaining,
            limit=limit,
            resets_in_seconds=reset_ts - int(time.time()),
        )
    
    # Store in Redis for admin monitoring
    await self.redis.setex(
        f"github:rate_limit:{self.installation_id}",
        300,  # 5-minute TTL
        json.dumps({"remaining": remaining, "limit": limit, "reset": reset_ts})
    )
```

---

## Consequences

### Positive
- ~10 GitHub API calls per review → 1 token exchange per 55 minutes per installation (instead of 10 per review)
- Token cache survives across Celery worker restarts (Redis persistence)
- If a token is revoked or expires early (e.g., installation permissions changed), the 403 retry logic handles it gracefully — invalidates cache and fetches a fresh token
- Rate limit monitoring built into every API call — proactive alerting before limits are hit

### Negative
- If Redis is down, every API call must exchange a token. The system continues functioning (graceful degradation) but with higher latency and rate limit consumption. **Mitigation:** Redis health check in Docker Compose; Redis persistence ensures fast restart recovery

### Security Note

Installation tokens in Redis are encrypted in transit (TLS to Redis in production) but stored as plaintext values. If the Redis instance is compromised, tokens are exposed. **Mitigation:** Redis must not be exposed on public network interfaces; Docker network isolation is enforced in Compose; tokens expire within 55 minutes even if stolen.
