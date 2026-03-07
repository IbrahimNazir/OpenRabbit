# ADR-0026: Two-Step Finding Deduplication

**Status:** Accepted
**Date:** 2026-03-07
**Phase:** 2 (Days 6–11)

## Context

With 5 pipeline stages potentially flagging issues, duplication is unavoidable: Stage 0 (linter) and Stage 2 (LLM) may both flag the same SQL injection. Stage 2 and Stage 4 may both comment on the same function. Without deduplication, the PR gets flooded with redundant comments, damaging developer experience.

## Decision

Two-step deduplication in Stage 5:

**Step 1 — Rule-based (no LLM, always runs):**
- Group findings by `(file_path, overlapping_line_range)` where "overlapping" means the ranges overlap or are within 3 lines of each other
- Within each group, keep only the finding with the highest severity; on tie, keep highest confidence
- Remove findings where `diff_position is None` (cannot be posted to GitHub)
- Remove findings below `config.severity_threshold` (configurable per repo via `.openrabbit.yaml`)
- Cap at 25 findings total (keep highest severity ones)

**Step 2 — LLM dedup (only if > 15 findings remain after Step 1):**
- Serialize remaining findings to JSON
- Use `PROMPT_SYNTHESIS` with the cheap model
- Parse the `"keep"` list from the response
- Apply the filter

Final sort order: `severity_order ASC` → `file_path ASC` → `line_start ASC`

## Consequences

**Positive:**
- Rule-based step handles 80%+ of duplicates at zero LLM cost
- LLM step only activates for noisy PRs (> 15 raw findings)
- Deterministic output for the common case

**Negative:**
- The "within 3 lines" heuristic may occasionally merge distinct issues that happen to be near each other
- LLM dedup step is non-deterministic (temperature > 0)
