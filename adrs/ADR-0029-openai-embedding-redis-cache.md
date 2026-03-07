# ADR-0029: OpenAI text-embedding-3-small with Redis Cache

**Status:** Accepted
**Date:** 2025-02-27
**Phase:** Phase 3 — RAG/Context Engine

---

## Context

Embedding API calls are the dominant cost driver for RAG. Most code in a repository is unchanged between PRs, so embeddings can be heavily cached. The cache must survive across review tasks (i.e., be persistent, not in-memory).

## Decision

Use **OpenAI `text-embedding-3-small`** (1536 dimensions, $0.02/1M tokens) with a **30-day Redis cache**.

Cache key: `emb:{sha256(text.encode()).hexdigest()[:16]}`
Cache TTL: `30 * 24 * 3600` seconds (30 days)

Embeddings are deterministic for the same input text — same code chunk always produces the same vector. This makes cache hit rate approach 100% for unchanged code after the first indexing run.

API calls are batched: up to 100 texts per OpenAI request (`EMBED_API_BATCH = 100`). Qdrant upserts are batched at 500 points per call (`QDRANT_UPSERT_BATCH = 500`).

A separate `openai_api_key` config field is added (distinct from the LLM provider keys) because embedding usage should be tracked and billed separately.

## Consequences

- ~70–90% cache hit rate on typical PRs after initial repository indexing
- Estimated cost: ~$0.005/PR after warmup (mostly NL query descriptions, not chunk re-embedding)
- Redis already deployed — no new infrastructure
- Cache miss latency: ~200ms for a 100-text batch (acceptable — RAG runs in parallel with summary)

## Alternatives Considered

| Option | Reason Rejected |
|--------|----------------|
| `text-embedding-3-large` | 3072 dims, 5× cost, marginal quality improvement for code retrieval |
| CodeSage / CodeBERT | Requires self-hosted inference server, adds GPU dependency |
| Sentence-transformers | CPU inference too slow for production batch indexing |
| No caching | Would cost ~$0.50/PR at full re-embedding per review |
