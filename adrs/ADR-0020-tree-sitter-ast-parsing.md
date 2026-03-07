# ADR-0020: Tree-sitter for AST Parsing

**Status:** Accepted
**Date:** 2026-03-07
**Phase:** 2 (Days 6–11)

## Context

Phase 1 treated code hunks as plain text and fed raw line content to the LLM. This misses critical structural context: which function a hunk belongs to, the enclosing class, import relationships. Without AST awareness, the LLM cannot reason about scope or cross-function impacts.

We need a language-aware parser that:
- Works for 6+ languages (Python, JS, TS, Go, Rust, Java)
- Runs in-process (no external server)
- Produces reliable AST node data quickly (< 50ms per file)
- Identifies syntax errors in LLM-generated code suggestions

## Decision

Use **Tree-sitter** with the official language-specific Python binding packages (`tree-sitter-python`, `tree-sitter-javascript`, `tree-sitter-typescript`, `tree-sitter-go`, `tree-sitter-rust`, `tree-sitter-java`).

Each language is loaded via its dedicated package:
```python
import tree_sitter_python as tspython
from tree_sitter import Language, Parser
PY_LANGUAGE = Language(tspython.language())
```

Parser instances are cached in a module-level dict (`_PARSER_CACHE`) to avoid re-initialization cost. Languages not in the supported set gracefully return `None` from `get_parser()`, triggering the sliding-window fallback.

## Consequences

**Positive:**
- Native bindings → very fast parsing (< 5ms for typical files)
- 100+ language support path (add more packages as needed)
- ERROR nodes in AST serve as reliable syntax error signals for suggestion validation
- No external service dependency

**Negative:**
- Adds ~7 packages to dependencies (~15 MB total)
- Tree-sitter v0.23+ changed the API — each language uses `Language(lang.language())` not the old `Language.build_library()` approach
- For dynamically typed languages (Python), import resolution is heuristic-based only

## Alternatives Considered

- **Pygments**: Fast lexer but no AST — can't identify function boundaries reliably
- **libCST** (Python only): More powerful for Python but not multi-language
- **Language Server Protocol**: Accurate but requires external server process per language
