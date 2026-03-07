# ADR-0023: Five-Stage Review Pipeline

**Status:** Accepted
**Date:** 2026-03-07
**Phase:** 2 (Days 6–11)

## Context

Phase 1 had a single stage: run LLM bug detection on each hunk sequentially. This is both too expensive (runs the LLM on everything including trivial changes) and too imprecise (no linter pre-filtering, no cross-file awareness, no deduplication).

## Decision

Implement a five-stage pipeline following the architecture document:

```
Stage 0: Static Analysis (subprocess, no LLM cost)
  → Run ruff/eslint/gofmt + gitleaks in sandboxed temp dirs
  → Filter findings to changed lines only

Stage 1: Summarization (LLM, cheap, sequential)
  → Summarize PR diff in 300 tokens
  → Extract risk_level — used to gate Stage 3

Stage 2: Bug & Security Detection (LLM, parallel across files)
  → HUNK_LEVEL for simple files (1-3 hunks, non-security)
  → FILE_LEVEL for security-critical files or files with >3 hunks
  → Max 20 LLM calls

Stage 3: Cross-File Impact (LLM, conditional)
  → Only runs if risk_level='high' OR function signature changed
  → Uses heuristic text search (Phase 2); symbol graph (Phase 4)

Stage 4: Style & Guidelines (LLM, parallel, cheap model)
  → Runs on each hunk independently
  → Skips hunks already covered by Stage 2
  → Skips test files

Stage 5: Synthesis & Deduplication
  → Rule-based dedup first (no LLM cost)
  → LLM dedup only if >15 findings remain
  → Cap at 25 findings
  → Sort: critical first, then file_path, then line_start
```

Stages 0 and 2-file analyses run via `asyncio.gather()` with a shared `Semaphore(5)` to limit concurrent LLM API calls.

## Consequences

**Positive:**
- Cheap operations (linters, rule-based dedup) run before expensive LLM calls
- Parallel execution keeps total latency under 90 seconds for typical PRs
- Clear separation of concerns — each stage is independently testable
- Stage 3 is only triggered when genuinely needed (cost control)

**Negative:**
- Stage 1 must complete before Stage 2 (sequential dependency for summary context)
- More complex orchestration than a flat loop

## Alternatives Considered

- **Fully agentic loop**: Too unpredictable and expensive for production
- **Single LLM pass**: Cheaper but misses cross-file issues and produces more noise
