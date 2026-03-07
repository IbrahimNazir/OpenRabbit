# ADR-0028: Qdrant as the Vector Database

**Status:** Accepted
**Date:** 2025-02-27
**Phase:** Phase 3 — RAG/Context Engine

---

## Context

Phase 3 requires storing and querying vector embeddings of every function and class in indexed repositories. The vector store must support:
- Self-hosted deployment (no cloud dependency)
- Python async client
- Filtered nearest-neighbour search (by `repo_id`, excluding changed files)
- Keyword/text search on payload fields (for `find_callers`)
- Efficient upsert of up to tens of thousands of chunks per repository

## Decision

Use **Qdrant** (self-hosted via Docker) as the vector database.

Two collections:
- `code_chunks`: vector_size=1536, distance=Cosine — stores function/class embeddings
- `past_findings`: vector_size=1536, distance=Cosine — stores past review findings for few-shot retrieval

Point ID scheme: `chunk_id` (16-char SHA256 hex) zero-padded to 32 chars → `uuid.UUID(chunk_id.ljust(32, '0'))`. This gives deterministic, collision-free UUIDs.

A text payload index on the `content` field of `code_chunks` enables keyword scroll for `find_callers` without a separate search infrastructure.

## Consequences

- Single Docker container (`qdrant/qdrant:latest`) added to `docker-compose.yml`
- `AsyncQdrantClient` used throughout — no blocking calls in async workers
- `ensure_collection()` called at task startup — creates collections idempotently
- If Qdrant is down: all RAG calls return empty lists, review proceeds as Phase 2

## Alternatives Considered

| Option | Reason Rejected |
|--------|----------------|
| Pinecone | Cloud-only, violates self-hosted requirement |
| Weaviate | Heavier operational footprint, GraphQL API less ergonomic for simple kNN |
| LanceDB | Embedded (no separate service), Python async client less mature |
| pgvector | Already using Postgres but requires manual index tuning; Qdrant's filtered search is faster |
