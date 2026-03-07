# ADR-0033: Natural Language Query Construction for RAG Retrieval

**Status:** Accepted
**Date:** 2025-02-27
**Phase:** Phase 3 — RAG/Context Engine

---

## Context

Vector similarity search works best when the query and the indexed documents are in the same semantic space. Raw code diffs are syntactically rich but semantically sparse — embedding `+    if user.role == 'admin':` doesn't match well against related authorization code that uses different variable names.

## Decision

Before embedding for retrieval, generate a **one-sentence natural language description** of the hunk change using a micro-LLM call (Haiku, `max_tokens=100`).

**Prompt:** `PROMPT_DESCRIBE_CHANGE` — asks for a single sentence with no JSON, no explanation.

**Cache:** `desc:{sha256(hunk_text.encode()).hexdigest()[:16]}`, TTL = 7 days. The same hunk in different PRs (e.g., rebases) returns the cached description.

**Fallback:** On any `LLMError` or timeout, use raw hunk text (first 200 chars) as the query. Retrieval quality degrades but never blocks.

**Hunk text extraction:** Only `line_type == "added"` lines from the first hunk, joined, truncated to 500 chars. This focuses on what was introduced, not what was removed.

## Consequences

- ~1 extra Haiku call per file per PR (cached on repeat reviews of same hunk)
- Estimated cost: <$0.001 per file (Haiku is ~20× cheaper than Sonnet)
- NL query embeddings match ~30% better than raw code for cross-language patterns
- The 7-day cache covers typical PR review cycles; stale after major rewrites (acceptable)

## Alternatives Considered

| Option | Reason Rejected |
|--------|----------------|
| Embed raw diff directly | Lower retrieval relevance for semantic patterns |
| Use function name as query | Misses cases where function was renamed; doesn't capture intent |
| Pre-compute descriptions during indexing | Doubles the indexing complexity; descriptions are review-time concerns |
