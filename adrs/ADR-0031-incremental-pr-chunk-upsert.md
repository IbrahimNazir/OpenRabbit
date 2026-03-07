# ADR-0031: Incremental PR-Time Chunk Upsert

**Status:** Accepted
**Date:** 2025-02-27
**Phase:** Phase 3 — RAG/Context Engine

---

## Context

Re-embedding an entire repository on every PR review would be prohibitively expensive and slow. However, the code index must stay fresh for changed files to ensure accurate retrieval.

## Decision

On every PR review, re-embed only chunks that **overlap with changed hunks** in the PR diff.

**Overlap check:** A chunk overlaps a hunk if:
```
chunk.start_line <= hunk.new_start + max(hunk.new_count - 1, 0)
AND chunk.end_line >= hunk.new_start
```

**Skip-if-current:** Before embedding, scroll Qdrant for existing chunks with matching `{file_path, commit_sha=head_sha}`. Chunks already indexed at `head_sha` are excluded from re-embedding.

**Deleted files:** Delete all Qdrant chunks by `{repo_id, file_path}` filter.

**Renamed files:** Treat as deleted + added.

This runs as a `PRIndexer.index_pr_changes()` call before the review pipeline, wrapped in try/except so a failure does not block the review.

## Consequences

- Only 5–15% of chunks per file need re-embedding per PR (typically the changed functions)
- Embedding costs near zero after initial indexing
- Index may be slightly stale for unchanged files (acceptable — full re-index on installation)
- `PRIndexer` requires `repo_full_name` at call time to fetch file content

## Alternatives Considered

| Option | Reason Rejected |
|--------|----------------|
| Re-embed full file on any change | 10–50× more embedding API calls |
| Webhook-driven incremental update | Push events don't include diff context needed for overlap check |
| Re-embed nothing (full-repo-only) | Index would be stale for files changed in the PR being reviewed |
