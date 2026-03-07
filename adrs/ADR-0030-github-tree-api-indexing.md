# ADR-0030: GitHub Tree API for Repository Indexing (No Git Clone)

**Status:** Accepted
**Date:** 2025-02-27
**Phase:** Phase 3 — RAG/Context Engine

---

## Context

Full repository indexing requires accessing every code file. Two approaches exist: git clone or GitHub's REST API. The system runs in Docker containers without persistent storage volumes for large repos.

## Decision

Use `GET /repos/{owner}/{repo}/git/trees/{sha}?recursive=1` to list all file paths in one API call, then fetch file content via `GET /repos/{owner}/{repo}/contents/{path}?ref={sha}` for each file.

**Filtering:**
- Code extensions only (sourced from `EXTENSION_TO_LANGUAGE` in `diff_parser.py`)
- Files larger than 500 KB skipped
- Skip patterns: `vendor/`, `node_modules/`, `*.min.js`, `_pb2.py`, `*.lock`, `generated/`, `__pycache__/`, `dist/`, `build/`, binary extensions

**Rate limiting:** Track call timestamps, enforce max 30 GitHub API calls/minute with `asyncio.sleep`.

**Batching:** 20 files processed in parallel per batch via `asyncio.gather`.

**Resume support:** Redis key `index_progress:{repo_id}` stores `last_processed_index`, allowing interrupted indexing to resume.

**Truncation:** If the tree API returns `truncated=true` (>100k objects), a warning is logged and the partial file list is used. Full recursive fetch is not implemented (uncommon case).

## Consequences

- No filesystem requirements beyond what linters already use
- Works entirely via the existing `GitHubClient._request()` and `get_file_content()` methods
- GitHub rate limit of 5000 req/hour means a 1000-file repo takes ~2 minutes
- Large repos (>3000 files) are handled by the resume mechanism

## Alternatives Considered

| Option | Reason Rejected |
|--------|----------------|
| `git clone` | Requires ephemeral disk storage, git binary in worker image, much higher I/O |
| GitHub GraphQL API | More complex, same rate limits, no advantage for bulk file fetching |
| Webhooks (push events) | Only covers incremental updates, not initial full indexing |
