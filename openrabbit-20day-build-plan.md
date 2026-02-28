# OpenRabbit: 20-Day Build Plan
## AI Code Reviewer — Complete Task Breakdown & Design Document

---

# SECTION 1: PROJECT OVERVIEW

## 1.1 Product Definition
**Name:** OpenRabbit (working name — rename before open-source launch)
**What it is:** A GitHub App that automatically reviews Pull Requests using AI, posting structured inline comments with bug findings, security issues, style violations, and one-click code suggestions.
**Open-source goal:** MIT-licensed, self-hostable, with a simple Docker Compose deploy.

## 1.2 Core User Story
> "As a developer, when I open a PR on GitHub, within 2 minutes I receive structured AI comments directly on the changed lines — identifying real bugs, security risks, and style issues — with one-click fix suggestions I can apply without leaving GitHub."

## 1.3 Non-Goals for This 20-Day Sprint
- GitLab / Bitbucket support (GitHub only)
- Billing / SaaS monetization layer
- Multi-region deployment
- Fine-tuned custom models
- Web dashboard (minimal admin only on Day 20)

---

# SECTION 2: FINAL ARCHITECTURE (TARGET STATE)

## 2.1 System Components

```
┌─────────────────────────────────────────────────────────────┐
│                      OPENRABBIT SYSTEM                       │
├──────────────┬──────────────┬───────────────┬───────────────┤
│  API Gateway │  Task Queue  │  Worker Pool  │  Storage      │
│  FastAPI     │  Redis       │  Celery       │  Postgres     │
│  Uvicorn     │  Streams     │  Workers      │  Qdrant       │
│  HMAC Auth   │  Dead Letter │  Async Pool   │  Redis Cache  │
├──────────────┴──────────────┴───────────────┴───────────────┤
│                     PROCESSING PIPELINE                       │
│  Diff Parser → Tree-sitter → Embedder → RAG → LLM Stages   │
│  Stage 0: Linters  Stage 1: Summary  Stage 2: Bugs          │
│  Stage 3: Cross-file  Stage 4: Style  Stage 5: Synthesis    │
├──────────────────────────────────────────────────────────────┤
│                     INTELLIGENCE LAYER                        │
│  Symbol Graph (NetworkX)  │  Conversation State (Redis)     │
│  Call-site Lookup         │  "Fix This" Suggestion Flow     │
└──────────────────────────────────────────────────────────────┘
```

## 2.2 Technology Stack (Final)

| Layer | Technology | Why |
|-------|-----------|-----|
| Language | Python 3.12 | Best AI/ML ecosystem |
| API Framework | FastAPI + Uvicorn | Async-native, fast |
| Task Queue | Redis Streams + Celery | Simple, battle-tested |
| Database | PostgreSQL 16 | Tenant/state storage |
| Vector DB | Qdrant (Docker) | Easy self-host, great Python SDK |
| Graph (in-process) | NetworkX | No extra service; sufficient for v1 |
| Cache | Redis 7 | Embeddings, sessions, rate limits |
| Code Parsing | tree-sitter + grammars | 100+ languages, AST-accurate |
| Embeddings | OpenAI text-embedding-3-small | Fast + cheap ($0.02/1M tokens) |
| LLM (fast) | Claude Haiku 3.5 | Hunk-level, cheap |
| LLM (main) | Claude Sonnet 4.5 | Bug/security analysis |
| Containerization | Docker + Docker Compose | Single-command deploy |
| GitHub Integration | PyGithub + Webhook HMAC | Official SDK |

## 2.3 Repository Structure (Final State)

```
openrabbit/
├── docker-compose.yml          # Full stack: app + postgres + redis + qdrant
├── .env.example                # All required env vars documented
├── README.md
├── pyproject.toml              # Poetry project definition
│
├── app/
│   ├── main.py                 # FastAPI app entrypoint
│   ├── config.py               # Settings (pydantic-settings)
│   │
│   ├── api/
│   │   ├── webhooks.py         # GitHub webhook receiver (HMAC validated)
│   │   ├── health.py           # /health endpoint
│   │   └── admin.py            # Basic admin endpoints (Day 20)
│   │
│   ├── core/
│   │   ├── github_client.py    # GitHub API wrapper (PyGithub)
│   │   ├── diff_parser.py      # Unified diff → DiffHunk objects
│   │   ├── line_mapper.py      # new_line → diff_position mapping
│   │   └── filter_engine.py    # Gatekeeper: bot/noise detection
│   │
│   ├── parsing/
│   │   ├── tree_sitter_parser.py   # AST extraction per language
│   │   ├── chunk_extractor.py      # Function/class chunk extraction
│   │   └── symbol_graph.py         # NetworkX symbol graph builder
│   │
│   ├── rag/
│   │   ├── embedder.py         # Embedding generation + Redis cache
│   │   ├── indexer.py          # Full repo indexing worker
│   │   ├── retriever.py        # Semantic search from Qdrant
│   │   └── context_builder.py  # Assembles full context packet
│   │
│   ├── pipeline/
│   │   ├── orchestrator.py     # Main pipeline: stages 0-5
│   │   ├── stage_0_linters.py  # Static analysis (subprocess-sandboxed)
│   │   ├── stage_1_summary.py  # PR summarization
│   │   ├── stage_2_bugs.py     # Bug + security detection
│   │   ├── stage_3_xfile.py    # Cross-file impact analysis
│   │   ├── stage_4_style.py    # Style + best practices
│   │   └── stage_5_synth.py    # Synthesis + deduplication
│   │
│   ├── llm/
│   │   ├── client.py           # Anthropic SDK wrapper + retry logic
│   │   ├── prompts.py          # All prompt templates
│   │   └── ast_validator.py    # Validate suggestions before posting
│   │
│   ├── conversation/
│   │   ├── state_store.py      # Redis-backed conversation state
│   │   └── reply_handler.py    # "Fix this" → suggestion generation
│   │
│   ├── tasks/
│   │   ├── celery_app.py       # Celery configuration
│   │   ├── review_task.py      # Main review task
│   │   ├── index_task.py       # Repository indexing task
│   │   └── reply_task.py       # PR comment reply task
│   │
│   └── models/
│       ├── database.py         # SQLAlchemy models + session
│       ├── pr_review.py        # PRReview, Finding models
│       └── tenant.py           # Installation/repo models
│
├── migrations/                 # Alembic DB migrations
│   └── versions/
│
└── tests/
    ├── fixtures/               # Sample diffs, webhook payloads
    ├── test_diff_parser.py
    ├── test_line_mapper.py
    ├── test_filter_engine.py
    └── test_pipeline.py
```

---

# SECTION 3: DATA MODELS

## 3.1 PostgreSQL Schema

```sql
-- GitHub App installation → maps to a "tenant"
CREATE TABLE installations (
    id BIGINT PRIMARY KEY,           -- GitHub installation_id
    account_login VARCHAR(255),      -- org or user name
    account_type VARCHAR(50),        -- 'Organization' or 'User'
    access_token TEXT,               -- encrypted installation token
    token_expires_at TIMESTAMP,
    config JSONB DEFAULT '{}',       -- user's .openrabbit.yaml merged config
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Repos connected to an installation
CREATE TABLE repositories (
    id BIGINT PRIMARY KEY,           -- GitHub repo_id
    installation_id BIGINT REFERENCES installations(id),
    full_name VARCHAR(500),          -- "owner/repo"
    default_branch VARCHAR(255),
    index_status VARCHAR(50) DEFAULT 'pending',  -- pending|indexing|ready|failed
    last_indexed_sha VARCHAR(40),
    indexed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- One record per PR review job
CREATE TABLE pr_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id BIGINT REFERENCES repositories(id),
    pr_number INTEGER NOT NULL,
    pr_title TEXT,
    head_sha VARCHAR(40),
    base_sha VARCHAR(40),
    status VARCHAR(50) DEFAULT 'queued',  -- queued|processing|completed|failed
    stage VARCHAR(50),                    -- current pipeline stage
    findings_count INTEGER DEFAULT 0,
    cost_usd NUMERIC(10,6) DEFAULT 0,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Individual findings posted as PR comments
CREATE TABLE findings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id UUID REFERENCES pr_reviews(id),
    file_path TEXT,
    line_start INTEGER,
    line_end INTEGER,
    diff_position INTEGER,
    severity VARCHAR(20),    -- critical|high|medium|low|info
    category VARCHAR(50),    -- bug|security|style|performance|docs
    title TEXT,
    body TEXT,
    suggestion_code TEXT,    -- if a code fix was generated
    github_comment_id BIGINT,
    was_applied BOOLEAN,
    was_dismissed BOOLEAN,
    created_at TIMESTAMP DEFAULT NOW()
);

-- PR comment thread state for conversation
CREATE TABLE conversation_threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    github_comment_id BIGINT UNIQUE,
    finding_id UUID REFERENCES findings(id),
    repo_id BIGINT,
    pr_number INTEGER,
    thread_state JSONB,    -- full conversation history + context
    updated_at TIMESTAMP DEFAULT NOW()
);
```

## 3.2 Key Python Data Classes

```python
# app/core/diff_parser.py
@dataclass
class DiffLine:
    content: str
    line_type: str      # 'added' | 'removed' | 'context'
    old_lineno: int | None
    new_lineno: int | None
    diff_position: int  # 1-indexed position within the full diff

@dataclass
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine]
    function_context: str | None   # from AST: enclosing function name

@dataclass
class FileDiff:
    filename: str
    status: str          # 'added' | 'modified' | 'removed' | 'renamed'
    old_filename: str | None
    language: str | None
    hunks: list[DiffHunk]
    additions: int
    deletions: int

# app/pipeline/orchestrator.py
@dataclass
class Finding:
    file_path: str
    line_start: int
    line_end: int
    diff_position: int
    severity: str
    category: str
    title: str
    body: str
    suggestion_code: str | None
    confidence: float   # 0.0 - 1.0; used for dedup/filtering

@dataclass
class ReviewResult:
    pr_summary: str
    findings: list[Finding]
    total_cost_usd: float
    stages_completed: list[str]
```

---

# SECTION 4: 20-DAY SPRINT PLAN

## Sprint Philosophy
- Each day has **4–6 concrete tasks** with exact deliverables
- End of each day = a **working, testable system** (nothing WIP overnight)
- Use Claude for all code generation — prompts provided per task
- Days 1–5: MVP (working end-to-end)
- Days 6–11: Quality pipeline (accurate, multi-stage)
- Days 12–16: RAG/Context (codebase-aware)
- Days 17–20: Intelligence + Ship

---

## ═══════════════════════════════════════
## PHASE 1: MVP (Days 1–5)
## Goal: A working GitHub App that posts real AI reviews
## ═══════════════════════════════════════

---

### DAY 1: Project Foundation & GitHub App Setup

**Goal:** The skeleton is running and GitHub can send you webhooks.

#### Task 1.1 — Project Scaffolding (60 min)
Create the full directory structure and Python project.

**Commands to run:**
```bash
mkdir openrabbit && cd openrabbit
pip install poetry
poetry init --no-interaction
poetry add fastapi uvicorn[standard] celery redis python-dotenv \
           pydantic-settings sqlalchemy asyncpg alembic \
           anthropic pygithub httpx python-multipart
poetry add --dev pytest pytest-asyncio httpx black ruff
```

Create `.env.example`:
```
GITHUB_APP_ID=
GITHUB_APP_PRIVATE_KEY_PATH=./private-key.pem
GITHUB_WEBHOOK_SECRET=
ANTHROPIC_API_KEY=
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/openrabbit
REDIS_URL=redis://localhost:6379
QDRANT_URL=http://localhost:6333
```

**Claude Prompt for this task:**
> "Create a FastAPI app in `app/main.py` and `app/config.py`. Config should use pydantic-settings to load all env vars from the .env file. The FastAPI app should have CORS configured, include routers for `/api/webhooks`, `/health`, and `/admin`. Include lifespan events to initialize DB connection and Redis on startup. No logic yet — just the skeleton with proper typing."

**Deliverable:** `python -m uvicorn app.main:app --reload` starts without errors.

---

#### Task 1.2 — GitHub App Registration (30 min)
This is manual — follow these exact steps:

1. Go to `github.com/settings/apps/new` (personal) or your org settings
2. **App name:** OpenRabbit (or your chosen name)
3. **Homepage URL:** `http://localhost:8000` (update later)
4. **Webhook URL:** Use [smee.io](https://smee.io) for local dev: `https://smee.io/your-unique-id`
5. **Webhook Secret:** Generate with `openssl rand -hex 32`, save to `.env`
6. **Permissions needed:**
   - Pull requests: Read & Write
   - Contents: Read
   - Metadata: Read
   - Commit statuses: Read & Write
7. **Subscribe to events:**
   - Pull request (opened, synchronize, reopened)
   - Pull request review comment (created)
   - Installation (created)
8. Download the **Private Key** (.pem file) → save as `./private-key.pem`
9. Note your **App ID** → save to `.env`
10. Install the app on a test repo

**Deliverable:** GitHub App installed, private key saved, webhook secret in `.env`.

---

#### Task 1.3 — Webhook Receiver with HMAC Validation (90 min)

**Claude Prompt:**
> "Create `app/api/webhooks.py` — a FastAPI router that handles POST requests to `/api/webhooks/github`. 
> Requirements:
> 1. Verify HMAC-SHA256 signature from `X-Hub-Signature-256` header using the `GITHUB_WEBHOOK_SECRET` env var. Return 403 if invalid.
> 2. Parse the webhook payload (JSON). Extract: `action`, `installation.id`, `repository.id`, `repository.full_name`, `pull_request.number` (if present), `pull_request.head.sha`, `pull_request.base.sha`.
> 3. Route events:
>    - `installation` action `created` → log 'new installation', return 200
>    - `pull_request` action in `['opened', 'synchronize', 'reopened']` → log the PR details, return 200
>    - `pull_request_review_comment` action `created` → log the comment, return 200
>    - All others → return 200 (no-op)
> 4. ALL processing after the 200 response (stub out async task calls for now)
> 5. Include full type hints and docstrings. Use Python logging, not print()."

**Deliverable:** `curl -X POST localhost:8000/api/webhooks/github` with a fake payload returns 403. With correct HMAC, returns 200.

---

#### Task 1.4 — Database Models & Migrations (60 min)

**Claude Prompt:**
> "Create SQLAlchemy 2.0 async models in `app/models/`. Create:
> - `app/models/database.py`: async engine, session factory, Base class
> - `app/models/tenant.py`: Installation and Repository models matching the SQL schema above
> - `app/models/pr_review.py`: PRReview, Finding, ConversationThread models
> Set up Alembic in `migrations/`. Create the initial migration. Include all indexes (repo_id + pr_number, finding review_id). Use `UUID` for primary keys where specified. All models should have `__repr__` methods."

**Deliverable:** `alembic upgrade head` runs without errors against a local PostgreSQL.

---

#### Task 1.5 — Docker Compose (30 min)

**Claude Prompt:**
> "Create a `docker-compose.yml` that spins up:
> 1. PostgreSQL 16 (port 5432, persistent volume)
> 2. Redis 7 Alpine (port 6379, persistent volume)  
> 3. Qdrant latest (port 6333, persistent volume)
> 4. A `smee-client` service using `node:18-alpine` that runs `npx smee-client` to forward webhook events to `http://app:8000/api/webhooks/github` (reads SMEE_URL from env)
> Include health checks for all services. Add a `app` service placeholder (commented out for now — we'll run the app locally during dev). Include a `networks` section so all services communicate."

**Deliverable:** `docker compose up -d` → all 3 services healthy.

---

**✅ Day 1 Done When:**
- Repo structure matches the target layout
- FastAPI starts, connects to DB and Redis
- GitHub app is registered and installed on a test repo
- Webhook endpoint validates HMAC and logs events
- Docker Compose runs all dependencies

---

### DAY 2: GitHub Client & Diff Fetching

**Goal:** Fetch the actual PR diff when a webhook arrives.

#### Task 2.1 — GitHub API Client (90 min)

**Claude Prompt:**
> "Create `app/core/github_client.py`. This is a wrapper around PyGithub and the raw GitHub API.
> Requirements:
> 1. Class `GitHubClient` that accepts an `installation_id` and generates an installation access token using the GitHub App private key (JWT-based auth). Cache the token in Redis with 55-minute TTL (tokens expire in 60 min).
> 2. Method `get_pr_diff(repo_full_name, pr_number) -> str`: Returns the raw unified diff text for a PR. Use the `/repos/{owner}/{repo}/pulls/{number}` endpoint with `Accept: application/vnd.github.v3.diff` header.
> 3. Method `get_file_content(repo_full_name, file_path, ref) -> str`: Returns decoded file content at a given SHA.
> 4. Method `post_review(repo_full_name, pr_number, head_sha, comments, body) -> dict`: Posts a pull request review with inline comments. Each comment has: `path`, `position`, `body`.
> 5. Method `post_review_comment(repo_full_name, pr_number, body, in_reply_to=None)`: Posts a top-level PR comment or a reply to an existing comment.
> 6. Full error handling: 404 (repo not found), 403 (token expired → refresh), 422 (invalid position). Raise domain-specific exceptions.
> Use `httpx.AsyncClient` for all API calls. Include rate limit handling (check `X-RateLimit-Remaining` header)."

**Deliverable:** Unit test that mocks GitHub API and verifies token caching, diff fetching, and comment posting.

---

#### Task 2.2 — Diff Parser (120 min)

This is one of the most critical components. Get it right.

**Claude Prompt:**
> "Create `app/core/diff_parser.py`. Implement a unified diff parser.
> 
> Data classes needed (already defined in Section 3.2): DiffLine, DiffHunk, FileDiff.
> 
> Main function: `parse_diff(diff_text: str) -> list[FileDiff]`
> 
> Requirements:
> 1. Parse `diff --git a/... b/...` file headers to get filenames and status (added/modified/removed/renamed)
> 2. Parse `@@ -old_start,old_count +new_start,new_count @@` hunk headers — also extract the optional function name that git sometimes includes after `@@` (e.g., `@@ -10,5 +10,7 @@ def my_function():`)
> 3. For each line in a hunk: track whether it's `+` (added), `-` (removed), or ` ` (context)
> 4. Compute `diff_position` for each line: this is the 1-indexed count of lines within the ENTIRE diff for this file (not just the hunk). GitHub's review API uses this number.
> 5. Track `new_lineno` for each added/context line, `old_lineno` for each removed/context line
> 6. Detect language from file extension (map common extensions to language names)
> 7. Skip binary files (marked as 'Binary files differ' in diff)
> 
> Also create `build_line_to_position_map(file_diff: FileDiff) -> dict[int, int]` that returns `{new_line_number: diff_position}` for all addable lines.
> 
> Write comprehensive tests in `tests/test_diff_parser.py` using sample diffs from `tests/fixtures/sample.diff`. Include edge cases: added files (no old path), renamed files, files with no newline at end."

**Deliverable:** Parser handles 10+ test diff fixtures correctly, including renames, new files, and multi-hunk files.

---

#### Task 2.3 — Filter Engine (Gatekeeper) (60 min)

**Claude Prompt:**
> "Create `app/core/filter_engine.py`. Implement the FilterEngine class.
> 
> Method `should_review(payload: dict) -> FilterResult` where FilterResult has fields: `should_process: bool`, `reason: str`, `queue: str` ('fast'|'slow'|'skip').
> 
> Filter rules (in order):
> 1. If PR author login ends in `[bot]` OR is in a known bot list (dependabot, renovate, snyk-bot, github-actions) → skip
> 2. If PR has label 'skip-ai-review' → skip
> 3. Fetch changed files list. If ALL files match no-review patterns: `*.md, *.txt, *.rst, *.png, *.jpg, *.svg, package-lock.json, yarn.lock, *.lock, *.sum, go.sum, Cargo.lock, poetry.lock` → skip
> 4. If changed files count > 50 → queue='slow'
> 5. If PR is in draft state → skip (with reason 'draft PR')
> 6. Otherwise → queue='fast', should_process=True
> 
> Also implement `get_reviewable_files(changed_files: list[str]) -> list[str]` that filters out binary, generated, and vendor files from the diff.
> 
> Include logging for every filter decision."

**Deliverable:** Filter correctly identifies bot PRs, lockfile-only PRs, and draft PRs from test payloads.

---

**✅ Day 2 Done When:**
- GitHub client fetches a real PR diff from your test repo
- Diff parser correctly parses the fetched diff into structured objects
- Filter engine correctly routes test payloads
- All 3 have unit tests passing

---

### DAY 3: Celery Workers & Basic LLM Review

**Goal:** The first end-to-end AI review, even if simple.

#### Task 3.1 — Celery Setup (45 min)

**Claude Prompt:**
> "Create `app/tasks/celery_app.py`. Configure Celery with:
> - Broker: Redis (REDIS_URL from config)
> - Result backend: Redis
> - Two queues: 'fast_lane' (default concurrency=4) and 'slow_lane' (concurrency=1)
> - Task time limits: soft=180s, hard=300s
> - Max retries: 3 with exponential backoff
> - Enable task events for monitoring
> - Configure serializer as JSON
> 
> Create `app/tasks/review_task.py` with a Celery task `run_pr_review(installation_id, repo_full_name, repo_id, pr_number, head_sha, base_sha)`. For now, the task should:
> 1. Log that review started
> 2. Create a PRReview record in the DB with status='processing'
> 3. Call a stub `run_pipeline(...)` function that returns a hardcoded ReviewResult
> 4. Update PRReview status to 'completed'
> 5. Handle exceptions: update status to 'failed', log the error
> 
> Update `app/api/webhooks.py` to call `run_pr_review.apply_async(args=[...], queue='fast_lane')` when a PR event arrives."

**Deliverable:** `celery -A app.tasks.celery_app worker -Q fast_lane,slow_lane` starts. Opening a PR triggers a queued task that logs correctly.

---

#### Task 3.2 — LLM Client with Retry Logic (60 min)

**Claude Prompt:**
> "Create `app/llm/client.py`. Implement `LLMClient` class wrapping the Anthropic SDK.
> 
> Requirements:
> 1. Method `complete(prompt: str, system: str, model: str, max_tokens: int, temperature: float) -> tuple[str, float]` — returns (response_text, cost_usd). Calculate cost based on input+output tokens × model pricing (Haiku: $0.25/$1.25 per 1M, Sonnet: $3/$15 per 1M).
> 2. Retry logic: on `anthropic.RateLimitError`, wait 60s and retry up to 3 times. On `anthropic.APIError` with status >= 500, retry with exponential backoff (5s, 15s, 45s).
> 3. Request timeout: 120 seconds.
> 4. Log every LLM call: model, input_tokens, output_tokens, cost_usd, duration_ms.
> 5. Method `complete_with_json(prompt, system, model, ...) -> tuple[dict, float]`: calls complete(), then strips markdown code fences and parses JSON. Raises `ValueError` if JSON is invalid. Retries once with a 'Return ONLY valid JSON' appended prompt if first attempt fails.
> 
> Models to support: `claude-haiku-4-5-20251001`, `claude-sonnet-4-5-20251001`
> Use async/await throughout."

---

#### Task 3.3 — Prompt Templates (60 min)

**Claude Prompt:**
> "Create `app/llm/prompts.py`. Define all prompt templates as Python string constants (use f-string templates where context needs to be injected).
> 
> Templates needed:
> 
> **SYSTEM_REVIEWER**: General system prompt establishing the AI as a senior code reviewer. Should specify: be concise, be specific with line numbers, only flag real issues not style opinions unless asked, never be condescending. (~150 words)
> 
> **PROMPT_SUMMARIZE**: Takes {pr_title}, {pr_description}, {diff_summary} (first 2000 chars of diff). Returns JSON: `{summary: str, key_changes: [str], risk_level: 'low'|'medium'|'high'}`
> 
> **PROMPT_BUG_DETECTION**: Takes {file_path}, {language}, {hunk_content}, {full_file_context} (optional). Returns JSON array: `[{line_start: int, line_end: int, severity: str, title: str, body: str, suggestion_code: str|null}]`
> 
> **PROMPT_STYLE_REVIEW**: Takes {file_path}, {language}, {hunk_content}, {custom_guidelines} (from config). Returns JSON array same format as bug detection but category='style'.
> 
> **PROMPT_CROSS_FILE_IMPACT**: Takes {changed_function}, {change_description}, {call_sites} (list of {file, line, code} dicts). Returns JSON: `{has_breaking_changes: bool, affected_call_sites: [{file, line, issue, suggestion}]}`
> 
> **PROMPT_SYNTHESIS**: Takes {all_findings_json}, {pr_summary}. Returns JSON: `{keep: [finding_id], remove_duplicates: [finding_id], final_summary: str}`
> 
> **PROMPT_FIX_THIS**: Takes {original_finding}, {file_content}, {line_start}, {line_end}. Returns JSON: `{fixed_code: str, explanation: str}`
> 
> Each prompt should include 1-2 few-shot examples of good/bad responses."

---

#### Task 3.4 — Basic Single-Pass Pipeline (90 min)

Wire everything together for a working (if simple) first review.

**Claude Prompt:**
> "Create `app/pipeline/orchestrator.py`. Implement `run_pipeline(github_client, repo_full_name, pr_number, head_sha, base_sha) -> ReviewResult`.
> 
> For Day 3 (MVP version — we'll expand in Phase 2), the pipeline should:
> 1. Fetch the raw diff using github_client.get_pr_diff()
> 2. Parse it with diff_parser.parse_diff()
> 3. Filter reviewable files using filter_engine.get_reviewable_files()
> 4. For each file's hunks (max 10 files, max 5 hunks per file for MVP):
>    a. Build a hunk_content string showing the changed lines with line numbers
>    b. Call LLMClient with PROMPT_BUG_DETECTION using claude-haiku (cheap for MVP)
>    c. Parse the JSON response into Finding objects
>    d. Map finding line numbers to diff_position using build_line_to_position_map()
> 5. Collect all findings, filter out any with diff_position=None (can't post these)
> 6. Return ReviewResult with all findings and total cost
> 
> Then update review_task.py to call this real pipeline and, after completion, call github_client.post_review() with all the findings as inline comments.
> 
> IMPORTANT: Include a fallback — if posting a specific comment fails (invalid position), skip it and continue with others. Never fail the whole review because of one bad line number."

**Deliverable:** Open a PR on your test repo → within 2 minutes, receive actual AI comments on the diff. Even if imperfect, this is the MVP milestone.

---

**✅ Day 3 Done When:**
- Celery workers run and process tasks
- Opening a PR triggers the pipeline
- Real AI comments appear on the PR within 2 minutes
- Cost is logged per review

---

### DAY 4: Installation Handling & Stability

**Goal:** Handle new installs, make the MVP robust.

#### Task 4.1 — Installation Webhook Handler (60 min)

**Claude Prompt:**
> "Update the webhook handler to properly handle `installation` events.
> When action='created': create an Installation record in the DB for each repository in the payload's `repositories` list. Create Repository records for each. Enqueue an `index_repository` task for each repo (stub for now — just log). Return 200.
> When action='deleted': mark Installation as inactive, stop processing its repos.
> When action='added' (repositories added to existing install): create new Repository records.
> Create `app/core/installation_service.py` with these handler methods. Include idempotency — if installation already exists, update rather than create duplicate."

#### Task 4.2 — Config File Support (45 min)

**Claude Prompt:**
> "Create `app/core/config_loader.py`. When a PR review starts, attempt to fetch `.openrabbit.yaml` from the repo root at the PR's base SHA. Parse this YAML config:
> ```yaml
> review:
>   enabled: true
>   language_rules:
>     python: true
>     javascript: true
>   custom_guidelines: |
>     - We use double quotes for strings
>     - All async functions must have error handling
>   ignore_patterns:
>     - 'tests/**'
>     - '*.generated.ts'
>   severity_threshold: medium  # only report medium+ findings
> ```
> Return a `ReviewConfig` dataclass. Use sensible defaults if file doesn't exist. Integrate config into the pipeline so ignore_patterns filter files and custom_guidelines are added to PROMPT_STYLE_REVIEW."

#### Task 4.3 — Error Handling & Dead Letter Queue (45 min)

**Claude Prompt:**
> "Improve error handling across the pipeline.
> 1. Create a `dead_letter` Celery queue. When a task fails all retries, post a top-level PR comment: 'OpenRabbit encountered an error during review. Please try again or open an issue at [repo link].' Then move task info to dead letter.
> 2. Add a circuit breaker: if LLM API fails 5 times in 10 minutes (track in Redis), pause new reviews for 5 minutes and notify in PR.
> 3. Add a maximum review duration guard: if the pipeline takes > 3 minutes, cancel gracefully and post what findings were completed so far.
> 4. Wrap all GitHub API calls in try/except — a 404 (repo deleted mid-review) or 403 (token issue) should be caught and logged, not crash the worker."

#### Task 4.4 — PR Comment: Summary Post (45 min)

**Claude Prompt:**
> "After all findings are posted, post a top-level PR comment that serves as the review summary. Format it as a GitHub-flavored markdown table:
> 
> ```
> ## 🐇 OpenRabbit AI Review
> 
> **Summary:** [pr_summary text]
> 
> | Severity | Count |
> |----------|-------|
> | 🔴 Critical | 0 |
> | 🟠 High | 2 |
> | 🟡 Medium | 5 |
> | 🔵 Low | 3 |
> 
> > Reviewed X files, Y hunks | Cost: $0.0023 | [Report an issue]
> ```
> 
> Implement this in a new method `post_summary_comment(github_client, repo_full_name, pr_number, review_result)` in `app/core/comment_formatter.py`."

---

**✅ Day 4 Done When:**
- Installing the app on a new repo creates DB records
- `.openrabbit.yaml` is respected
- Failed reviews post a user-friendly error comment
- Summary comment appears at the top of every completed review

---

### DAY 5: MVP Polish & E2E Validation

**Goal:** The MVP works reliably on 5 different real PR scenarios.

#### Task 5.1 — Webhook Idempotency (30 min)
**Claude Prompt:**
> "Add idempotency to the webhook handler. Before enqueuing a review task, check Redis for a key `review:{repo_id}:{pr_number}:{head_sha}`. If it exists, the review is already running or done — return 200 without re-queueing. Set this key when enqueuing with TTL=2 hours. This prevents duplicate reviews from webhook retries."

#### Task 5.2 — Testing Against Real PRs (120 min)
Create 5 test PRs on your test repository with these characteristics:
1. Simple Python bug (off-by-one, missing null check)
2. TypeScript/JavaScript file with a React component
3. A PR with only `.md` changes (should be skipped)
4. A PR from a branch that only modifies `package-lock.json` (should be skipped)
5. A multi-file PR with 3+ changed files

Verify: correct files reviewed, accurate line numbers, no crashes, summary comment posted.

#### Task 5.3 — Logging & Basic Observability (60 min)
**Claude Prompt:**
> "Set up structured logging using Python's `structlog` library. Every log entry should include: `timestamp`, `level`, `service` (worker/api), `installation_id`, `repo`, `pr_number` where applicable, `task_id`, `duration_ms` for timed operations.
> 
> Add timing instrumentation to: webhook handler, each pipeline stage, LLM calls, GitHub API calls.
> 
> Create a simple `/admin/stats` endpoint (protected by a secret header) returning JSON: active workers, reviews today, total cost today, error rate."

#### Task 5.4 — README & Quick Start (45 min)
**Claude Prompt:**
> "Write a comprehensive README.md for the openrabbit project. Include:
> 1. What it does (with a screenshot placeholder)
> 2. Self-hosting in 5 commands (docker compose + GitHub App setup)
> 3. Configuration reference (.openrabbit.yaml)
> 4. Architecture overview (simple ASCII diagram)
> 5. Contributing guide
> 6. License (MIT)
> Make it compelling — this is what will attract open-source contributors."

---

**✅ Phase 1 Complete When:**
- All 5 test scenarios work correctly
- No crashes over 20 consecutive test PRs
- README explains how to self-host

---

## ═══════════════════════════════════════
## PHASE 2: QUALITY PIPELINE (Days 6–11)
## Goal: Multi-stage analysis, Tree-sitter, accurate suggestions
## ═══════════════════════════════════════

---

### DAY 6: Tree-sitter Integration

**Goal:** Parse code into AST chunks for smarter analysis.

#### Task 6.1 — Tree-sitter Setup (60 min)
**Claude Prompt:**
> "Set up tree-sitter in the project. Install: `tree-sitter`, `tree-sitter-python`, `tree-sitter-javascript`, `tree-sitter-typescript`, `tree-sitter-go`, `tree-sitter-rust`, `tree-sitter-java`.
> 
> Create `app/parsing/tree_sitter_parser.py`. Implement `TreeSitterParser` class with:
> 1. `get_parser(language: str) -> Parser | None` — returns the right parser for a language name, cached in memory. Return None for unsupported languages.
> 2. `parse_file(source_code: str, language: str) -> Tree | None`
> 3. `extract_function_nodes(tree: Tree, source_code: str) -> list[dict]` — returns list of `{name, start_line, end_line, start_byte, end_byte, node_type}` for all function_definition, method_definition, arrow_function, class_definition nodes.
> 4. `get_enclosing_function(tree: Tree, line_number: int, source_code: str) -> str | None` — returns the name of the function/method that contains a given line number.
> 5. `extract_imports(tree: Tree, source_code: str) -> list[str]` — returns list of imported module names.
> Support: python, javascript, typescript, go, rust, java. Use pattern matching on node types, not hardcoded logic."

#### Task 6.2 — Semantic Chunk Extractor (90 min)
**Claude Prompt:**
> "Create `app/parsing/chunk_extractor.py`. Implement `extract_chunks(file_content: str, file_path: str) -> list[CodeChunk]`.
> 
> `CodeChunk` dataclass: `{chunk_id: str, file_path: str, chunk_type: str, name: str, content: str, start_line: int, end_line: int, language: str}`
> 
> Strategy:
> 1. Detect language from file_path extension
> 2. If TreeSitter supports language: use extract_function_nodes() to get semantic boundaries. Each function/class = one chunk.
> 3. If function is > 100 lines: split into sub-chunks at blank-line boundaries, overlap by 5 lines.
> 4. If language not supported or parsing fails: fall back to sliding window (512 tokens, 128 token overlap). Use tiktoken to count tokens.
> 5. Always include file-level chunk (first 50 lines = imports and class declarations).
> 6. chunk_id = sha256(file_path + name + start_line)[:16]
> 
> Write tests for Python, JS, and TS files."

#### Task 6.3 — Enrich Diff Hunks with AST Context (60 min)
**Claude Prompt:**
> "Update `app/core/diff_parser.py` and the pipeline to enrich each DiffHunk with AST context.
> 
> Add to the pipeline, after fetching the diff:
> 1. For each FileDiff, fetch the full file content at head_sha
> 2. Run TreeSitterParser to get the AST
> 3. For each DiffHunk, call `get_enclosing_function(tree, hunk.new_start)` to find which function the hunk is in
> 4. Store this as `hunk.function_context`
> 5. Also extract a 'scope context' string: 5 lines before the hunk's start + the hunk itself + 5 lines after. This is the 'smart context window' around every hunk.
> 
> Update DiffHunk dataclass to include: `function_context: str | None`, `scope_context: str`, `full_file_content: str | None`."

---

**✅ Day 6 Done When:**
- Tree-sitter parses Python, JS, TS files correctly
- Each hunk knows which function it belongs to
- Chunks are extracted from sample files with correct boundaries

---

### DAY 7: Multi-Stage Pipeline — Stages 0, 1

#### Task 7.1 — Stage 0: Linter Integration (90 min)
**Claude Prompt:**
> "Create `app/pipeline/stage_0_linters.py`. Implement `run_linters(file_path: str, file_content: str, language: str) -> list[LinterFinding]`.
> 
> `LinterFinding`: `{tool: str, rule: str, line: int, message: str, severity: str}`
> 
> Implementation:
> 1. Write file_content to a temp file in /tmp/openrabbit_lint/{uuid}/
> 2. Run the appropriate linter as a subprocess with 10s timeout:
>    - Python: `ruff check --output-format json {file}` (install ruff)
>    - JavaScript/TypeScript: `npx eslint --no-eslintrc -c '{}' --format json {file}` (basic rules only if no config)
>    - Go: `gofmt -l {file}` (check for format issues)
>    - All: `gitleaks detect --source {dir} --no-git` (secret detection)
> 3. Parse stdout JSON into LinterFinding objects
> 4. Clean up temp files in finally block
> 5. Catch subprocess errors gracefully — linter failures should NOT fail the review
> 6. Return only findings where line is within the changed hunks (don't report pre-existing issues)
> 
> Critical: All file I/O must happen in isolated temp directories, never in shared space."

#### Task 7.2 — Stage 1: Summarization (45 min)
**Claude Prompt:**
> "Create `app/pipeline/stage_1_summary.py`. Implement `run_summarization(diff_text: str, pr_title: str, pr_description: str, llm_client: LLMClient) -> SummaryResult`.
> 
> `SummaryResult`: `{summary: str, key_changes: list[str], risk_level: str, cost_usd: float}`
> 
> Use PROMPT_SUMMARIZE with claude-haiku-4-5-20251001. Pass only the first 3000 characters of the diff to keep costs low.
> 
> The summary will be used:
> 1. As the opening paragraph of the top-level PR comment
> 2. As context prepended to all subsequent stage prompts
> 
> Add a `risk_multiplier` to the ReviewConfig: if risk_level='high', enable stage 3 (cross-file) automatically."

#### Task 7.3 — Refactor Orchestrator to Staged Architecture (90 min)
**Claude Prompt:**
> "Refactor `app/pipeline/orchestrator.py` to be a true staged pipeline.
> 
> ```python
> async def run_pipeline(ctx: ReviewContext) -> ReviewResult:
>     # Stage 0: Static analysis (parallel across files)
>     linter_findings = await run_stage_0(ctx)
>     
>     # Stage 1: Summarization  
>     summary = await run_stage_1(ctx)
>     ctx.summary = summary  # inject into context for later stages
>     
>     # Stage 2: Bug + Security (parallel across hunks)
>     bug_findings = await run_stage_2(ctx)
>     
>     # Stage 3: Cross-file (conditional, agentic)
>     xfile_findings = await run_stage_3(ctx) if ctx.should_run_stage_3() else []
>     
>     # Stage 4: Style (parallel, cheap model)
>     style_findings = await run_stage_4(ctx)
>     
>     # Stage 5: Synthesis + dedup
>     final_findings = await run_stage_5(
>         linter_findings + bug_findings + xfile_findings + style_findings, ctx
>     )
>     
>     return ReviewResult(pr_summary=summary.summary, findings=final_findings, ...)
> ```
> 
> `ReviewContext` dataclass holds: github_client, repo_full_name, pr_number, head_sha, base_sha, config, file_diffs, summary (added after stage 1).
> 
> Use `asyncio.gather()` for parallel stages. Include a semaphore limiting max 5 concurrent LLM calls to avoid rate limits."

---

**✅ Day 7 Done When:**
- Pipeline runs all 5 stages in correct order
- Linters run without crashing even on unsupported languages
- Summary appears at top of review comment

---

### DAY 8: Stages 2 & 4 — Bug Detection & Style

#### Task 8.1 — Stage 2: Bug & Security Detection (120 min)
**Claude Prompt:**
> "Create `app/pipeline/stage_2_bugs.py`. Implement `run_bug_detection(ctx: ReviewContext) -> list[Finding]`.
> 
> Logic:
> 1. Group hunks by file
> 2. For each file: decide analysis depth:
>    - If file matches security_critical patterns (auth, password, token, payment, crypto, secret): → FILE_LEVEL (use full file content, use claude-sonnet-4-5-20251001)
>    - If file has > 3 changed hunks: → FILE_LEVEL
>    - Otherwise: → HUNK_LEVEL for each hunk independently (use claude-haiku-4-5-20251001)
> 3. Build the prompt with: language, hunk_content, scope_context, summary (from stage 1), linter findings for this file
> 4. Parse JSON response into Finding objects
> 5. Map line_start/line_end to diff_position using build_line_to_position_map()
> 6. Set confidence=0.9 for security findings, 0.8 for bugs, to help stage 5 filtering
> 
> Run all file analyses in parallel with asyncio.gather() + semaphore(5).
> Total max LLM calls for this stage: 20 (truncate if PR is huge)."

#### Task 8.2 — Stage 4: Style Analysis (60 min)
**Claude Prompt:**
> "Create `app/pipeline/stage_4_style.py`. Implement `run_style_review(ctx: ReviewContext) -> list[Finding]`.
> 
> This stage uses ONLY claude-haiku-4-5-20251001 (cheapest model).
> It runs on each changed hunk independently.
> It checks: naming conventions, function length (flag if >50 lines), code duplication hints, missing docstrings/comments, custom guidelines from config.
> 
> Important filtering: 
> - If a finding from stage 2 already covers the same line range, skip it (avoid duplicates)
> - Minimum severity for style findings: 'low' — never 'critical' or 'high'
> - Don't run style review on test files (path contains 'test', 'spec', '__test__')
> 
> This stage is optional — if config has `review.style: false`, skip entirely."

---

### DAY 9: Stage 3 & 5 — Cross-File & Synthesis

#### Task 9.1 — Stage 3: Cross-File Impact (90 min)

This requires the symbol graph (built in Phase 4), so for now, implement a simplified version using text search.

**Claude Prompt:**
> "Create `app/pipeline/stage_3_xfile.py`. Implement `run_cross_file_analysis(ctx: ReviewContext) -> list[Finding]`.
> 
> For Phase 2 (before symbol graph): use a heuristic approach:
> 1. For each changed function (identified via AST): extract the function name
> 2. Search for usages of that function name in other files of the PR (files that were NOT changed but are in the diff context)
> 3. For each usage found: include 10 lines of context around it
> 4. Run LLM with PROMPT_CROSS_FILE_IMPACT: 'Function X was changed like this. Here are call sites. Are there breaking changes?'
> 5. Only run this stage if: risk_level='high' OR a function signature changed (detect signature changes by checking if the function definition line itself is in the added/removed lines)
> 
> Mark findings from this stage as category='breaking-change', severity='high' or 'critical'."

#### Task 9.2 — Stage 5: Synthesis & Deduplication (90 min)
**Claude Prompt:**
> "Create `app/pipeline/stage_5_synth.py`. Implement `run_synthesis(all_findings: list[Finding], ctx: ReviewContext) -> list[Finding]`.
> 
> Two-step deduplication:
> 
> Step 1 — Rule-based dedup (fast, no LLM cost):
> - Group findings by file + line range (overlap within 3 lines = same issue)
> - Keep the one with highest severity / highest confidence
> - Remove any finding where diff_position is None (can't post it)
> - Remove findings below config's severity_threshold
> - Cap total findings at 25 (keep highest severity ones) to avoid comment spam
> 
> Step 2 — LLM synthesis (only if >15 findings remain):
> - Use claude-haiku with PROMPT_SYNTHESIS
> - Pass JSON of all remaining findings
> - Ask: 'Remove duplicates and false positives. Return list of IDs to keep.'
> - Apply the filter
> 
> Return final list sorted by: critical first, then by file_path, then by line_start."

---

### DAY 10: AST Validator & Line Number Accuracy

#### Task 10.1 — AST Validator for Code Suggestions (90 min)
**Claude Prompt:**
> "Create `app/llm/ast_validator.py`. Implement `validate_suggestion(code: str, language: str) -> ValidationResult`.
> 
> `ValidationResult`: `{is_valid: bool, error: str | None, fixed_code: str | None}`
> 
> For each language:
> 1. Parse with Tree-sitter
> 2. Walk the AST looking for ERROR nodes (Tree-sitter marks syntax errors this way)
> 3. If ERROR nodes found: attempt auto-fix by stripping leading/trailing whitespace, fixing indentation (use Python's `textwrap.dedent`)
> 4. Re-parse the fixed version
> 5. If still invalid: return is_valid=False
> 
> Update the pipeline: before including `suggestion_code` in a Finding, run validate_suggestion(). If invalid, set suggestion_code=None and append to the body: '(Note: A code fix was suggested but could not be validated — please review manually.)'
> 
> Also validate that the suggestion is for the correct line range: it must not be shorter than (line_end - line_start - 2) lines (the LLM shouldn't be returning one-line fixes for 20-line blocks)."

#### Task 10.2 — Line Number Accuracy Testing (60 min)
Write comprehensive tests specifically for line mapping:

**Claude Prompt:**
> "Create `tests/test_line_mapper.py`. Write tests for `build_line_to_position_map()` using 10 different real-world diff scenarios:
> 1. New file (all lines are additions)
> 2. File with only deletions (no additions)
> 3. File with multiple hunks far apart
> 4. File with adjacent hunks
> 5. Renamed file
> 6. File with context lines
> 7. File with 'no newline at end' marker
> 
> For each scenario: verify that the diff_position values returned match what GitHub's API actually expects. Cross-reference with GitHub's API docs for pull request review comments.
> 
> Also test that posting a comment at an invalid position (on a removed line, or after the diff ends) correctly returns None from the mapper."

#### Task 10.3 — Multi-line Suggestion Support (45 min)
**Claude Prompt:**
> "Update `app/core/github_client.py` post_review() method to support multi-line comments.
> GitHub's API supports `start_line` and `line` for multi-line comments, and `start_side`/`side` must be set to 'RIGHT' for additions.
> 
> Update the Finding model and comment posting logic:
> - If finding.line_start == finding.line_end: post as single-line comment
> - If finding.line_start < finding.line_end: post as multi-line comment with start_line and line both mapped via the position map
> - Validate that both start and end positions are in the same hunk (GitHub rejects cross-hunk multi-line comments)
> - For GitHub suggestions: format as ```suggestion\\n{code}\\n``` inside the comment body. Only valid for single-hunk ranges."

---

### DAY 11: Phase 2 Integration & Testing

#### Task 11.1 — Integration Test Suite (90 min)
**Claude Prompt:**
> "Create an integration test suite in `tests/integration/`. Use pytest-asyncio.
> 
> Create fixtures in `tests/fixtures/`:
> - `test_pr_simple.json`: GitHub PR webhook payload + diff for a 3-file Python PR
> - `test_pr_security.json`: PR with a SQL injection vulnerability
> - `test_pr_bot.json`: PR from dependabot (should be filtered)
> - `test_pr_typescript.json`: TypeScript React component PR
> 
> Test `test_full_pipeline.py`:
> 1. Test that bot PR produces no findings and no LLM calls
> 2. Test that security PR produces at least 1 high/critical finding
> 3. Test that all finding line numbers have valid diff_positions
> 4. Test that no invalid AST suggestions make it to the output
> 5. Mock GitHub API and Anthropic API (record real responses as fixtures)"

#### Task 11.2 — Performance Baseline (45 min)
Run the full pipeline against 10 sample PRs and measure:
- Average review time (target: <90 seconds)
- Average cost per PR (target: <$0.10)
- Finding accuracy (manual review of 5 PRs)

Document results in `BENCHMARKS.md`.

---

**✅ Phase 2 Complete When:**
- All 5 stages run correctly in order
- AST validation prevents invalid suggestions
- Line numbers are accurate (test suite passes)
- Average review completes in <90 seconds

---

## ═══════════════════════════════════════
## PHASE 3: RAG/CONTEXT ENGINE (Days 12–16)
## Goal: Reviews are codebase-aware, not just diff-aware
## ═══════════════════════════════════════

---

### DAY 12: Qdrant Setup & Embedding Pipeline

#### Task 12.1 — Qdrant Collection Setup (45 min)
**Claude Prompt:**
> "Create `app/rag/embedder.py`. Set up Qdrant integration.
> 
> Initialize Qdrant collection `code_chunks` with:
> - Vector size: 1536 (text-embedding-3-small dimensions)
> - Distance: Cosine
> - Payload schema: `{repo_id, file_path, chunk_id, chunk_type, name, language, start_line, end_line, commit_sha}`
> 
> Implement `EmbeddingService` class:
> 1. `embed_text(text: str) -> list[float]`: Call OpenAI embeddings API. Cache result in Redis with key `emb:{sha256(text)[:16]}`, TTL=30 days. Return cached value if exists.
> 2. `embed_batch(texts: list[str]) -> list[list[float]]`: Batch up to 100 texts in a single OpenAI API call (more efficient). Use chunked batching.
> 3. `upsert_chunks(chunks: list[CodeChunk], repo_id: int, commit_sha: str)`: Embed and upsert to Qdrant. Use Qdrant's batch upsert (500 vectors per batch).
> 4. `delete_repo_chunks(repo_id: int)`: Delete all vectors for a repo (used when repo is removed).
> 
> Calculate and log embedding cost: text-embedding-3-small costs $0.02/1M tokens."

#### Task 12.2 — Incremental Chunk Upsert for PRs (60 min)
**Claude Prompt:**
> "Create `app/rag/pr_indexer.py`. Implement `index_pr_changes(file_diffs: list[FileDiff], repo_id: int, head_sha: str, github_client: GitHubClient) -> int`.
> 
> This runs during every PR review (before the LLM stages):
> 1. For each modified/added file in the diff:
>    a. Fetch full file content at head_sha
>    b. Extract chunks using chunk_extractor.extract_chunks()
>    c. Identify which chunks overlap with the changed hunks (by line range)
>    d. Only re-embed the changed chunks (not the whole file)
>    e. Upsert changed chunks to Qdrant (overwriting old vectors for same chunk_id)
> 2. For deleted files: delete their chunks from Qdrant
> 3. Return count of chunks updated
> 
> Optimization: before fetching file content, check if chunk_id already exists in Qdrant at the same commit_sha — if so, skip (the embedding is already current)."

---

### DAY 13: Full Repository Indexing

#### Task 13.1 — Repository Indexing Task (120 min)
**Claude Prompt:**
> "Create `app/tasks/index_task.py` and `app/rag/indexer.py`. Implement full repository indexing.
> 
> `index_repository(installation_id, repo_full_name, repo_id)` Celery task:
> 
> 1. Update repo index_status to 'indexing'
> 2. Use GitHub API to get the default branch's tree (recursive):
>    `GET /repos/{owner}/{repo}/git/trees/{sha}?recursive=1`
>    This returns ALL files in the repo without cloning.
> 3. Filter files: keep only code files (<500KB each), skip generated/vendor patterns
> 4. For each file (process in batches of 20):
>    a. Fetch content via API: `GET /repos/{owner}/{repo}/contents/{path}?ref={sha}`
>    b. Extract chunks
>    c. Embed + upsert
> 5. Update progress in Redis: `index_progress:{repo_id}` = {total, done, status}
> 6. On completion: update repo.index_status='ready', repo.last_indexed_sha=sha
> 7. Post a comment on the most recent open PR (if any): 'OpenRabbit has finished indexing your repository. Future reviews will include codebase-wide context.'
> 
> Rate limiting: max 30 GitHub API calls/minute. Add asyncio.sleep() between batches.
> For large repos (>1000 files): process in background, continue even if interrupted — use Redis to track progress and resume."

#### Task 13.2 — Progress Endpoint (30 min)
**Claude Prompt:**
> "Add `GET /admin/repos/{repo_id}/index-status` endpoint that returns the current indexing progress from Redis. Include: `{status, total_files, indexed_files, percent_complete, started_at, eta_seconds}`. Use this to show progress to users."

---

### DAY 14: Semantic Retrieval

#### Task 14.1 — Context Retriever (90 min)
**Claude Prompt:**
> "Create `app/rag/retriever.py`. Implement `ContextRetriever` class.
> 
> Method `find_relevant_context(query: str, repo_id: int, exclude_files: list[str], top_k: int = 5) -> list[RetrievedChunk]`:
> 1. Embed the query
> 2. Query Qdrant with filter: `must: [{key: 'repo_id', match: {value: repo_id}}]`
> 3. Exclude chunks from files in exclude_files (those are already in the diff)
> 4. Return top_k results above score threshold 0.75
> 
> `RetrievedChunk`: `{chunk_id, file_path, name, content, score, start_line, end_line}`
> 
> Method `find_callers(function_name: str, repo_id: int, exclude_files: list[str]) -> list[RetrievedChunk]`:
> 1. Query: embed `f'call site for {function_name}({function_name}()'`
> 2. Also do a keyword-based search: filter Qdrant payload for chunks containing function_name in content (use scroll API with payload filter)
> 3. Merge + deduplicate results
> 4. Return top 10 (sorted by score)
> 
> This is the Phase 3 version of cross-file lookup — Phase 4 will replace with symbol graph."

#### Task 14.2 — Context Builder Integration (60 min)
**Claude Prompt:**
> "Create `app/rag/context_builder.py`. Implement `build_review_context(file_diff: FileDiff, retriever: ContextRetriever, repo_id: int) -> EnrichedContext`.
> 
> `EnrichedContext`: `{file_diff, relevant_chunks: list[RetrievedChunk], caller_chunks: list[RetrievedChunk]}`
> 
> For each changed file:
> 1. Build a query from the changed function names + hunk contents
> 2. Retrieve relevant_chunks: 'What other code in this repo is related to these changes?'
> 3. For each function whose signature changed (detect from AST): retrieve caller_chunks
> 
> Update Stage 2 and Stage 3 prompts to include the retrieved context:
> ```
> ## Codebase Context (retrieved from full repo)
> Related code in `{chunk.file_path}` (similarity: {chunk.score:.2f}):
> ```{language}
> {chunk.content}
> ```
> ```
> 
> Limit total context to 4000 tokens (use tiktoken to measure). Truncate if needed, prioritizing higher-scored chunks."

---

### DAY 15: RAG Quality & Relevance Tuning

#### Task 15.1 — Query Construction Improvement (60 min)
**Claude Prompt:**
> "Improve query construction in context_builder.py. Instead of using raw code as the query, generate a natural language description of the change:
> 
> Before embedding for retrieval, run a micro-LLM call (claude-haiku, max 100 tokens):
> PROMPT: 'In one sentence, describe what this code change does: {hunk_content[:500}}'
> Use this natural language description as the embedding query.
> 
> This significantly improves retrieval relevance because natural language embeddings match better than code embeddings.
> 
> Cache the description in Redis (key: `desc:{sha256(hunk_content)[:16]}`) to avoid re-generating for the same hunk."

#### Task 15.2 — Similar Past Reviews Retrieval (45 min)
**Claude Prompt:**
> "Add a second Qdrant collection: `past_findings`. After each review completes, upsert all findings:
> - Vector: embedding of the finding's body text
> - Payload: `{repo_id, org_id, category, severity, language, was_applied, was_dismissed}`
> 
> In context_builder.py, add `find_similar_past_findings(hunk_content, repo_id, org_id) -> list[PastFinding]`.
> 
> Inject top 3 similar past findings into the Stage 2 prompt as few-shot examples:
> 'In a similar code pattern, we previously found: [finding]. The developer [applied|dismissed] this.'
> 
> This creates organizational memory — the system gets smarter about what each team cares about."

#### Task 15.3 — Embedding Cost Report (30 min)
Add a cost tracking middleware that logs: embedding API calls, embedding cache hits/misses, Qdrant query count. Display in `/admin/stats`.

---

### DAY 16: RAG Integration & Phase 3 Testing

#### Task 16.1 — End-to-End RAG Test (90 min)
Test scenario: 
1. Index a test repository (use a public GitHub repo with Python or JS code)
2. Open a PR that changes a function signature
3. Verify that the review correctly identifies call sites in other files (via RAG retrieval)
4. Measure: retrieval latency, relevance of retrieved chunks

**Claude Prompt:**
> "Create `tests/test_rag_integration.py`. Tests:
> 1. Index 50 chunks from sample files into Qdrant
> 2. Query for a known function name → verify it appears in top 5 results
> 3. Query for a calling pattern → verify the call site is retrieved
> 4. Test that excluded files are not returned
> 5. Test score threshold filtering (nothing below 0.75 returned)
> 6. Test that embedding cache reduces API calls on repeated queries"

---

**✅ Phase 3 Complete When:**
- New repo installations trigger indexing
- PR reviews include retrieved context from the full codebase
- Cross-file call sites are found via RAG (even without symbol graph)
- Embedding costs are <$0.005 per review (due to caching)

---

## ═══════════════════════════════════════
## PHASE 4: INTELLIGENCE LAYER (Days 17–20)
## Goal: Symbol graph, conversation state, ship it
## ═══════════════════════════════════════

---

### DAY 17: Symbol Graph

**Goal:** Structural code understanding for cross-file analysis.

#### Task 17.1 — NetworkX Symbol Graph (120 min)
**Claude Prompt:**
> "Create `app/parsing/symbol_graph.py`. Implement `SymbolGraph` class using NetworkX.
> 
> Nodes: symbols (functions, classes, variables). Node attributes: `{name, file_path, start_line, end_line, kind: 'function'|'class'|'variable'|'import'}`
> Edge types: `CALLS`, `IMPORTS`, `DEFINES`, `EXTENDS`
> 
> Methods:
> 1. `build_from_chunks(chunks: list[CodeChunk], file_contents: dict[str, str]) -> SymbolGraph`:
>    For each file: parse AST → extract all symbol definitions + their call sites + imports
>    Build edges based on: function calls (by name matching), import statements
> 2. `find_callers(function_name: str, file_path: str) -> list[CallSite]`:
>    Returns all nodes with a `CALLS` edge to the given symbol
> 3. `find_dependencies(file_path: str) -> list[str]`:
>    Returns all files that the given file imports from
> 4. `get_impact_scope(changed_symbols: list[str]) -> list[CallSite]`:
>    Traverses `CALLS` edges up to 2 hops to find transitive callers
> 
> `CallSite`: `{file_path, function_name, line_number, code_snippet}`
> 
> Persist the graph to Redis as JSON (serialized with networkx.node_link_data()) with key `symbol_graph:{repo_id}:{commit_sha}`. TTL: 7 days.
> Load from Redis on startup of each review worker."

#### Task 17.2 — Symbol Graph Builder Task (60 min)
**Claude Prompt:**
> "Create `app/tasks/graph_task.py`. Implement `build_symbol_graph(repo_id, commit_sha)` Celery task.
> 
> This runs after initial indexing completes:
> 1. Load all chunks for this repo from Qdrant (use scroll API to get all)
> 2. Fetch file contents for each unique file_path from GitHub API
> 3. Run SymbolGraph.build_from_chunks()
> 4. Serialize and store in Redis
> 5. Update Repository.graph_status='ready'
> 
> Also create an incremental updater: `update_symbol_graph_for_pr(repo_id, file_diffs)` — only reprocess changed files, update the graph in Redis."

#### Task 17.3 — Replace RAG Call-Site Lookup with Graph Lookup (45 min)
**Claude Prompt:**
> "Update `app/pipeline/stage_3_xfile.py` to use the symbol graph when available.
> 
> Updated logic:
> 1. Load symbol graph from Redis for this repo
> 2. If graph available: use `graph.get_impact_scope(changed_symbols)` → much more accurate than RAG search
> 3. If graph not available (repo not fully indexed): fall back to RAG-based retrieval from day 14
> 4. Fetch actual code snippets for each call site from GitHub API (or from Qdrant payload)
> 5. Run LLM with PROMPT_CROSS_FILE_IMPACT
> 
> This is the agentic escalation: the system decides at runtime whether it needs to fetch more context, based on what it finds in the graph."

---

### DAY 18: Conversation State & Reply Handling

#### Task 18.1 — Conversation State Store (60 min)
**Claude Prompt:**
> "Create `app/conversation/state_store.py`. Implement `ConversationStateStore` backed by Redis + Postgres.
> 
> When a finding is posted to GitHub, create a conversation thread record:
> - Key in Redis: `thread:{github_comment_id}` → JSON of ConversationState
> - Also persist to `conversation_threads` table
> 
> `ConversationState`:
> ```python
> @dataclass
> class ConversationState:
>     comment_id: int
>     finding_id: str
>     repo_full_name: str
>     pr_number: int
>     file_path: str
>     line_start: int
>     line_end: int
>     diff_position: int
>     head_sha: str
>     language: str
>     original_finding_body: str
>     full_file_content: str  # file content at time of review
>     conversation_history: list[dict]  # [{role, content}]
> ```
> 
> Methods: `save(state)`, `load(comment_id) -> ConversationState | None`, `append_message(comment_id, role, content)`, `delete(comment_id)`"

#### Task 18.2 — PR Comment Reply Handler (120 min)
**Claude Prompt:**
> "Create `app/conversation/reply_handler.py`. Implement `handle_reply(comment_payload: dict) -> None`.
> 
> This is called when `pull_request_review_comment` event fires with action='created'.
> 
> Flow:
> 1. Check if comment is `in_reply_to_id` (a reply to another comment) — if no in_reply_to_id, it's a top-level new comment, ignore.
> 2. Load ConversationState for `in_reply_to_id`. If not found, ignore (not our comment).
> 3. Parse the developer's message. Classify intent:
>    - 'fix this' / 'apply fix' / 'please fix' → GENERATE_FIX
>    - 'explain' / 'why' / 'what does this mean' → EXPLAIN
>    - 'dismiss' / 'ignore' / 'not applicable' → DISMISS
>    - anything else → CONVERSE (treat as follow-up question)
> 4. Route to the appropriate handler:
>    - GENERATE_FIX: see Task 18.3
>    - EXPLAIN: re-run Stage 2 with more verbose prompt, reply with explanation
>    - DISMISS: mark finding as dismissed in DB, reply 'Got it, dismissed this finding.'
>    - CONVERSE: add to conversation history, ask LLM to continue the thread
> 5. Append developer's message and AI response to conversation_history
> 6. Save updated state"

#### Task 18.3 — 'Fix This' Suggestion Generation (90 min)
**Claude Prompt:**
> "Create the GENERATE_FIX handler in reply_handler.py.
> 
> Flow:
> 1. Load the current file content at the PR's head_sha (not the cached version — the dev may have pushed changes)
> 2. Extract lines line_start to line_end from the current file
> 3. Build prompt with PROMPT_FIX_THIS: include original finding, current lines, full function context
> 4. Call claude-sonnet-4-5-20251001 (use better model for code generation)
> 5. Parse response: extract `fixed_code` and `explanation`
> 6. Validate fixed_code with AST validator
> 7. If valid:
>    - Format as GitHub suggestion: wrap in ` ```suggestion ` block
>    - Post as reply to the original finding comment with `start_line` and `line` parameters
>    - Update finding.suggestion_code in DB
> 8. If invalid (AST fails):
>    - Post reply with the explanation only: 'Here is how to fix this: {explanation}. [Code snippet here]'
>    - Never post syntactically invalid code suggestions
> 9. Reply to developer within the same thread"

---

### DAY 19: Performance, Cost Controls & Final QA

#### Task 19.1 — Model Cascade Cost Controls (60 min)
**Claude Prompt:**
> "Implement the full model cascade in orchestrator.py.
> 
> For each hunk, calculate a `complexity_score` (0-10):
> - Lines changed: 1-10 lines = +1, 11-30 = +3, 31+ = +5
> - File category security/auth/payment = +4
> - Has function signature change = +3
> - Has nested control flow (detected by AST depth) = +2
> 
> Model routing:
> - complexity_score 0-3: claude-haiku-4-5-20251001 (cheapest)
> - complexity_score 4-7: claude-sonnet-4-5-20251001
> - complexity_score 8-10: claude-sonnet-4-5-20251001 with extended thinking (or specific deep-review request)
> 
> Add a per-review cost cap: if accumulated LLM cost exceeds $0.50 (configurable), stop generating new findings for additional files and post a notice: 'Review truncated after {n} files due to cost limit. Configure in .openrabbit.yaml.'
> 
> Track per-org monthly cost in Redis. Alert when approaching limits."

#### Task 19.2 — Comprehensive QA Test Run (120 min)
Run the full system against 10 real PRs spanning:
1. Simple Python fix
2. TypeScript React component
3. Go file change
4. Security vulnerability (test with intentionally bad code in private test repo)
5. Large PR (20+ files) → should route to slow lane
6. Bot PR → should be skipped
7. Reply 'fix this' on a generated comment → suggestion should appear
8. Reply 'explain' → explanation should follow
9. PR with a changed function signature → cross-file impact should be detected
10. Re-open a PR → should get fresh review (idempotency check)

Document all issues found and fix them.

#### Task 19.3 — Rate Limit & Error Scenario Testing (60 min)
**Claude Prompt:**
> "Write tests for failure scenarios:
> 1. GitHub API returns 403 on token expiry → verify token refresh and retry
> 2. Anthropic API returns 429 rate limit → verify exponential backoff and retry
> 3. Qdrant is down → verify pipeline continues without RAG context (graceful degradation)
> 4. Invalid diff_position causes GitHub API rejection → verify the finding is skipped but others are posted
> 5. Tree-sitter fails to parse a file → verify fallback to sliding window chunking
> 6. Worker crashes mid-review → verify Redis idempotency key prevents duplicate review on restart"

---

### DAY 20: Admin UI, Docker Polish & Open-Source Release

#### Task 20.1 — Minimal Admin Dashboard (90 min)
**Claude Prompt:**
> "Create a minimal admin dashboard as a single HTML file served by FastAPI at `/admin`.
> Use vanilla HTML + Tailwind CDN (no build step). The dashboard shows:
> - Real-time active workers count (poll `/admin/stats` every 5s)
> - Reviews today: count, success rate, avg cost, avg duration
> - Recent reviews table: repo, PR number, status, cost, duration, timestamp
> - Per-repo index status
> - Error log (last 20 errors)
> Protect with HTTP Basic Auth (ADMIN_PASSWORD env var). No JavaScript frameworks — keep it simple and fast to load."

#### Task 20.2 — Docker Compose: Full Stack (60 min)
**Claude Prompt:**
> "Update docker-compose.yml to include the application services:
> 1. `api`: FastAPI app — Dockerfile with Python 3.12-slim, runs uvicorn on port 8000
> 2. `worker_fast`: Celery worker consuming fast_lane queue, concurrency=4
> 3. `worker_slow`: Celery worker consuming slow_lane queue, concurrency=1
> 4. `worker_index`: Celery worker for index tasks, concurrency=2
> 5. `flower`: Celery Flower monitoring UI on port 5555
> 
> Create `Dockerfile` with multi-stage build (builder + runtime). Use non-root user. Health check on /health endpoint.
> 
> Create `.env.example` documenting every env var with descriptions and example values.
> 
> Create `scripts/setup.sh`: interactive script that walks a user through: generating webhook secret, creating GitHub App (opens browser), saving private key, configuring .env."

#### Task 20.3 — Open-Source Release Checklist (60 min)

Complete before public release:

- [ ] Remove any hardcoded test credentials from codebase
- [ ] Ensure all secrets come from env vars
- [ ] Add `.gitignore` covering .env, *.pem, __pycache__, .venv
- [ ] Add `CONTRIBUTING.md` with: development setup, how to run tests, PR guidelines
- [ ] Add `SECURITY.md`: responsible disclosure policy
- [ ] Add GitHub Actions CI: `.github/workflows/ci.yml` — runs ruff, mypy, pytest on every PR
- [ ] Add issue templates: bug report, feature request
- [ ] Tag v0.1.0 release
- [ ] Push to GitHub with MIT license

**Claude Prompt for CI:**
> "Create `.github/workflows/ci.yml`. On push and PR to main:
> 1. Lint with ruff
> 2. Type check with mypy
> 3. Run pytest (unit tests only, skip integration)
> 4. Build Docker image to verify Dockerfile is valid
> Use Python 3.12, cache pip dependencies."

---

**✅ Phase 4 Complete / Project Done When:**
- Symbol graph correctly identifies breaking changes across files
- 'Fix this' generates valid, one-click applicable code suggestions
- All 10 QA scenarios pass
- Docker Compose deploys the full stack in one command
- GitHub repo is public with clean README

---

# SECTION 5: HOW TO USE CLAUDE FOR EACH DAY

## 5.1 Effective Claude Prompting for This Project

**For every coding session:**
1. Start a new Claude conversation for each major module (don't let context grow stale)
2. Always paste the relevant data classes and interfaces before asking for implementation
3. Provide the full file path context: "This goes in `app/core/diff_parser.py` which imports from..."
4. Ask for tests alongside implementation: "Also write a pytest test file for this"
5. After getting code, ask: "What edge cases does this miss? What would break it in production?"

## 5.2 Daily Claude Session Pattern

```
Session 1 (morning): Architecture review
→ "Here's what I built yesterday. Does the integration between [A] and [B] look correct? Any issues?"

Session 2 (implementation): Core coding
→ Use the exact prompts from each task above

Session 3 (debugging): Fix issues
→ "I got this error: [paste full traceback]. Here's the relevant code: [paste code]. How do I fix it?"

Session 4 (review): Code quality
→ "Review this implementation for: correctness, error handling gaps, performance issues, security concerns"
```

## 5.3 Key Claude Prompts for Debugging

**When tests fail:**
> "This pytest test is failing with this error: {error}. Here's the test: {test}. Here's the implementation: {code}. Walk me through what's wrong."

**When GitHub API behaves unexpectedly:**
> "I'm getting this response from the GitHub PR Review API: {response}. I'm trying to post a comment at position {pos} on this diff: {diff}. What am I doing wrong?"

**When LLM responses are inconsistent:**
> "My Stage 2 prompt sometimes returns invalid JSON. Here's the prompt: {prompt}. Here's an example bad response: {response}. How do I make the prompt more reliable?"

---

# SECTION 6: COST PROJECTION

## Estimated Monthly Cost (1000 PRs/month)

| Component | Cost |
|-----------|------|
| Claude Haiku (Stage 1, 4 — 80% of calls) | ~$8 |
| Claude Sonnet (Stage 2, 3, 5 — 20% of calls) | ~$45 |
| OpenAI Embeddings (with 80% cache hit rate) | ~$2 |
| Postgres (managed, small) | ~$25 |
| Qdrant Cloud (or self-hosted free) | $0–$25 |
| Redis | ~$10 |
| Compute (2 small VMs for workers) | ~$40 |
| **Total** | **~$130/month for 1000 PRs** |
| **Per PR** | **~$0.13** |

Self-hosted (own server) brings compute to ~$40/month total.

---

# SECTION 7: RISK REGISTER

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| GitHub API rate limits | Medium | High | Token rotation, request caching |
| LLM API outage | Low | High | Fallback model (OpenAI ↔ Anthropic), graceful degradation |
| Incorrect line numbers → rejected comments | High (early) | Medium | Comprehensive line mapper tests, skip-don't-fail |
| LLM hallucination in suggestions | Medium | High | AST validation, confidence threshold |
| Embedding drift (same code, different vector) | Low | Low | SHA-based cache keys prevent this |
| Worker crash mid-review | Medium | Medium | Celery retry + Redis idempotency key |
| Repo with 10,000 files (indexing timeout) | Low | Medium | Batch processing with resume capability |

---

*Last updated: Day 0. Update this document daily with what was actually built vs. planned.*
