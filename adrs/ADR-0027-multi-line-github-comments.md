# ADR-0027: Multi-line GitHub PR Review Comments

**Status:** Accepted
**Date:** 2026-03-07
**Phase:** 2 (Days 6–11)

## Context

Phase 1's `post_review()` only supported single-line comments (`position` parameter). When a finding spans multiple lines (e.g., a function body from line 10 to 25), GitHub's API supports multi-line review comments using `start_line` + `line` + `start_side` + `side` parameters. Without this, multi-line findings are posted on only the last line, losing context.

## Decision

Extend `post_review()` to build the correct comment payload based on the finding's `line_start` vs `line_end`:

```python
# Single-line (line_start == line_end)
comment = {
    "path": finding.file_path,
    "position": diff_position,
    "body": body_text,
}

# Multi-line (line_start < line_end)
comment = {
    "path": finding.file_path,
    "start_line": line_start,
    "line": line_end,
    "start_side": "RIGHT",
    "side": "RIGHT",
    "body": body_text,
}
```

**Validation before posting multi-line:**
- Both `start_line` and `line` must appear in the position map (commentable lines)
- Both must be within the same diff hunk (GitHub rejects cross-hunk multi-line comments)
- If validation fails, fall back to single-line comment at `line_end`

**Suggestion blocks:** Only posted for single-hunk, same-range findings. Multi-line suggestions spanning multiple hunks are not supported by GitHub's API.

The `build_review_comment()` helper in `github_client.py` encapsulates this logic, taking a `Finding` and the `line_map` dict.

## Consequences

**Positive:**
- Multi-line findings are visually clear in GitHub's PR diff view
- Correct line anchoring for function-level findings

**Negative:**
- Cross-hunk multi-line comments silently fall back to single-line — the developer may not realize the issue spans multiple hunks
- GitHub's multi-line comment API is only supported in the reviews endpoint (not the issue comments endpoint)
