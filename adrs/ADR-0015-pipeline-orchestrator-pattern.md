# ADR-0015: Pipeline Orchestrator Pattern

**Status:** Accepted
**Date:** 2025-03-01
**Context:** Day 3 — MVP single-pass review pipeline

## Decision

Implement a **sequential single-pass pipeline** for the MVP:

```
Fetch Diff → Parse → Filter Files → LLM per Hunk → Map Positions → Post Review
```

### MVP Limits
- Max 10 files per review (skip remaining with notice).
- Max 5 hunks per file.
- Use Claude Haiku for all hunk analysis (cheapest model).

### Graceful Degradation
- If a single comment's `diff_position` is invalid → skip that comment, continue.
- If GitHub rejects a review (422) → retry without the offending comment.
- If LLM returns invalid JSON for one hunk → skip that hunk, continue.
- Never fail the entire review because of one bad finding.

### Output
- Post inline review comments via GitHub's Pull Request Review API.
- Post a top-level summary comment with severity table.
- Update PRReview DB record with findings count and total cost.

## Consequences
- Simple, debuggable, single-pass flow for MVP.
- Ready to evolve into multi-stage pipeline (Days 7+).
- Fault-tolerant: partial reviews are better than no reviews.
