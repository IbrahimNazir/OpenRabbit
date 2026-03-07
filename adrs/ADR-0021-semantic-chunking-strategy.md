# ADR-0021: Semantic Chunking Strategy

**Status:** Accepted
**Date:** 2026-03-07
**Phase:** 2 (Days 6–11)

## Context

For the RAG pipeline (Phase 3) and for enriching LLM context (Phase 2), we need to split files into logical units. Naive token-count chunking destroys function boundaries and makes vector search less accurate.

## Decision

Three-tier chunking strategy:

**Tier 1 — Semantic (Tree-sitter, preferred):**
- Parse AST, extract all `function_definition`, `method_definition`, `class_definition`, `arrow_function` nodes
- Each node = one chunk
- If a function is > 100 lines: split at blank-line boundaries with 5-line overlap between sub-chunks

**Tier 2 — Sliding window (fallback for unsupported languages or parse failures):**
- 512-token window with 128-token overlap
- Uses `tiktoken` (cl100k_base encoding) for token counting
- Ensures LLM context windows are never exceeded

**Tier 3 — File-level (always included):**
- First 50 lines of every file = one additional chunk
- Captures imports and class declarations that appear before first function
- Always added regardless of Tier 1/2

**Chunk ID:** `SHA256(file_path + name + str(start_line))[:16]` — deterministic for cache keying.

## Consequences

**Positive:**
- Function-boundary chunks match how developers reason about code
- Improves RAG retrieval relevance (Phase 3)
- Graceful degradation via sliding window fallback
- File-level chunk ensures import context always available

**Negative:**
- Very large functions (> 300 lines) produce large chunks that may exceed context windows
- Sliding window fallback loses semantic structure
- tiktoken adds another dependency

## Alternatives Considered

- **Fixed token size only**: Simple but destroys function boundaries
- **Line-count fixed size**: Language-agnostic but semantically poor
