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
