# ADR-0025: AST Validation Before Posting Code Suggestions

**Status:** Accepted
**Date:** 2026-03-07
**Phase:** 2 (Days 6–11)

## Context

LLMs frequently generate syntactically invalid code suggestions. Posting a broken code fix to GitHub as a "suggestion" block is worse than no suggestion — it erodes developer trust and could be accidentally applied.

## Decision

Before any `suggestion_code` is included in a `Finding`, validate it through `ast_validator.validate_suggestion(code, language)`:

1. Parse with Tree-sitter
2. Walk the AST for `ERROR` nodes (Tree-sitter's syntax error marker)
3. On error: attempt auto-fix via `textwrap.dedent()` + strip, re-parse
4. If still invalid: set `suggestion_code = None`, append to finding body: `"(Note: A code fix was suggested but could not be validated — please review manually.)"`
5. Also validate line count: suggestion must be ≥ `(line_end - line_start - 2)` lines (prevents one-line fixes for 20-line blocks)

`ValidationResult` dataclass: `is_valid: bool, error: str | None, fixed_code: str | None`

For unsupported languages (no Tree-sitter parser), skip validation and include the suggestion as-is.

## Consequences

**Positive:**
- Zero syntactically invalid suggestions reach GitHub
- Auto-fix handles common indentation issues from LLM output
- Developer trust preserved

**Negative:**
- Valid suggestions for unsupported languages bypass validation
- Very creative (but valid) code may be rejected by Tree-sitter's strict parser in some edge cases

## Alternatives Considered

- **Python `ast.parse()` only**: Python-only; doesn't cover JS/TS/Go
- **No validation**: Unacceptable; broken suggestions harm trust
- **LLM self-verification**: Too expensive and circular
