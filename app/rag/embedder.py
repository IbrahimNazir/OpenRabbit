"""Embedding service: HuggingFace Qwen3-Embedding-8B + Redis cache + Qdrant upsert.

Implements ADR-0028 (Qdrant collection schema) and ADR-0029 (Redis-cached embeddings).

All Qdrant operations use AsyncQdrantClient.  The client instance is created once
in __init__ and shared across calls.  Call ``aclose()`` when done.

Embedding cache key: ``emb:{sha256(text)[:16]}``  TTL: 30 days.
Point IDs: chunk_id (16-char hex) zero-padded to 32 chars → UUID.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qdrant_models

from app.core.exceptions import EmbeddingError, QdrantError
from app.parsing.chunk_extractor import CodeChunk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------------------

QDRANT_COLLECTION = "code_chunks"
PAST_FINDINGS_COLLECTION = "past_findings"
QDRANT_VECTOR_SIZE = 1024
EMBEDDING_MODEL_ID = "mixedbread-ai/mxbai-embed-large-v1"
HF_API_BASE = "https://router.huggingface.co/hf-inference"
EMBED_CACHE_TTL = 30 * 24 * 3600  # 30 days
QDRANT_UPSERT_BATCH = 500
EMBED_API_BATCH = 32  # HF Inference API has tighter batch limits


# ---------------------------------------------------------------------------
#  Data classes
# ---------------------------------------------------------------------------


@dataclass
class EmbedStats:
    """Statistics from a batch embedding + upsert operation."""

    tokens_used: int = 0
    cost_usd: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0
    chunks_upserted: int = 0


# ---------------------------------------------------------------------------
#  Helper: chunk_id → deterministic UUID
# ---------------------------------------------------------------------------


def _chunk_id_to_uuid(chunk_id: str) -> uuid.UUID:
    """Zero-pad chunk_id (16-char hex) to 32 chars and convert to UUID."""
    padded = chunk_id.ljust(32, "0")
    return uuid.UUID(padded)


# ---------------------------------------------------------------------------
#  EmbeddingService
# ---------------------------------------------------------------------------


class EmbeddingService:
    """Handles embedding generation, Redis caching, and Qdrant storage.

    Uses the HuggingFace Inference API with Qwen3-Embedding-8B.

    Args:
        redis: An async Redis client instance (redis.asyncio).
        qdrant_url: URL of the Qdrant server (e.g. 'http://localhost:6333').
        huggingface_api_key: HuggingFace API token.
    """

    def __init__(self, redis: Any, qdrant_url: str, huggingface_api_key: str) -> None:
        self._redis = redis
        self._qdrant = AsyncQdrantClient(url=qdrant_url, timeout=30)
        self._hf_client = httpx.AsyncClient(
            base_url=HF_API_BASE,
            headers={"Authorization": f"Bearer {huggingface_api_key}"},
            timeout=180.0,  # Large models can be slow on cold start
        )

    # ------------------------------------------------------------------
    #  HuggingFace Inference API
    # ------------------------------------------------------------------

    async def _call_hf_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Call HuggingFace Inference API for embeddings.

        Sends texts to the feature-extraction pipeline and returns
        one embedding vector per input text.
        """
        response = await self._hf_client.post(
            f"/models/{EMBEDDING_MODEL_ID}",
            json={"inputs": texts},
        )

        if response.status_code == 503:
            body = response.json()
            estimated = body.get("estimated_time", "unknown")
            raise EmbeddingError(
                f"Model {EMBEDDING_MODEL_ID} is loading (est. {estimated}s). "
                "The Celery task will retry automatically."
            )

        if response.status_code >= 400:
            logger.error(
                "HF embedding API error",
                extra={"status": response.status_code, "body": response.text[:500]},
            )
            raise EmbeddingError(
                f"HF Inference API error {response.status_code}: {response.text[:300]}"
            )

        data = response.json()

        # Normalize response format
        if not data:
            return []
        # Single flat vector [0.1, 0.2, ...] for single input
        if isinstance(data[0], (int, float)):
            return [data]
        return data

    # ------------------------------------------------------------------
    #  Collection setup
    # ------------------------------------------------------------------

    async def ensure_collection(self, collection_name: str = QDRANT_COLLECTION) -> None:
        """Create the Qdrant collection if it does not exist.

        If the collection exists but has a different vector size (e.g. from a
        previous provider), it is recreated.

        For ``code_chunks``, also creates a text payload index on the
        ``content`` field to support keyword-based caller lookup (ADR-0032).
        """
        try:
            collections = await self._qdrant.get_collections()
            existing = {c.name for c in collections.collections}
            if collection_name in existing:
                # Verify vector size matches
                info = await self._qdrant.get_collection(collection_name)
                current_size = info.config.params.vectors.size  # type: ignore[union-attr]
                if current_size != QDRANT_VECTOR_SIZE:
                    logger.warning(
                        "Collection '%s' vector size mismatch (%d != %d) — recreating",
                        collection_name,
                        current_size,
                        QDRANT_VECTOR_SIZE,
                    )
                    await self._qdrant.delete_collection(collection_name)
                else:
                    return

            await self._qdrant.create_collection(
                collection_name=collection_name,
                vectors_config=qdrant_models.VectorParams(
                    size=QDRANT_VECTOR_SIZE,
                    distance=qdrant_models.Distance.COSINE,
                ),
            )
            logger.info("Created Qdrant collection: %s", collection_name)

            # Text index on 'content' for keyword search (code_chunks only)
            if collection_name == QDRANT_COLLECTION:
                await self._qdrant.create_payload_index(
                    collection_name=collection_name,
                    field_name="content",
                    field_schema=qdrant_models.PayloadSchemaType.TEXT,
                )
                logger.debug("Created text payload index on 'content' for %s", collection_name)

        except Exception as exc:
            raise QdrantError(f"Failed to ensure collection '{collection_name}'") from exc

    # ------------------------------------------------------------------
    #  Embedding
    # ------------------------------------------------------------------

    async def embed_text(self, text: str) -> list[float]:
        """Embed a single text string with Redis caching.

        Cache key: ``emb:{sha256(text.encode())[:16]}``
        TTL: EMBED_CACHE_TTL (30 days).

        Returns the embedding vector as a list of floats.
        """
        cache_key = f"emb:{sha256(text.encode()).hexdigest()[:16]}"
        try:
            cached = await self._redis.get(cache_key)
            if cached:
                return json.loads(cached)  # type: ignore[return-value]
        except Exception:
            logger.debug("Redis cache read failed for embedding key %s", cache_key)

        try:
            vectors = await self._call_hf_embeddings([text])
            vector = vectors[0]
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"HF embedding call failed: {exc}") from exc

        try:
            await self._redis.setex(cache_key, EMBED_CACHE_TTL, json.dumps(vector))
        except Exception:
            logger.debug("Redis cache write failed for embedding key %s", cache_key)

        return vector

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts, using Redis cache for known texts.

        Batches API calls in groups of EMBED_API_BATCH.
        Returns vectors in the same order as the input list.
        """
        if not texts:
            return []

        # Phase 1: check cache for all texts
        cache_keys = [f"emb:{sha256(t.encode()).hexdigest()[:16]}" for t in texts]
        results: list[list[float] | None] = [None] * len(texts)
        miss_indices: list[int] = []

        for i, key in enumerate(cache_keys):
            try:
                cached = await self._redis.get(key)
                if cached:
                    results[i] = json.loads(cached)
                else:
                    miss_indices.append(i)
            except Exception:
                miss_indices.append(i)

        if not miss_indices:
            return [v for v in results if v is not None]

        # Phase 2: batch embed cache misses
        miss_texts = [texts[i] for i in miss_indices]
        miss_vectors: list[list[float]] = []

        for batch_start in range(0, len(miss_texts), EMBED_API_BATCH):
            batch = miss_texts[batch_start : batch_start + EMBED_API_BATCH]
            try:
                batch_vectors = await self._call_hf_embeddings(batch)
            except EmbeddingError:
                raise
            except Exception as exc:
                raise EmbeddingError(f"HF batch embedding failed: {exc}") from exc

            miss_vectors.extend(batch_vectors)

            # Store new embeddings in Redis
            for text, vec in zip(batch, batch_vectors):
                key = f"emb:{sha256(text.encode()).hexdigest()[:16]}"
                try:
                    await self._redis.setex(key, EMBED_CACHE_TTL, json.dumps(vec))
                except Exception:
                    pass

        # Merge results
        for miss_idx, vector in zip(miss_indices, miss_vectors):
            results[miss_idx] = vector

        return [v for v in results if v is not None]

    # ------------------------------------------------------------------
    #  Qdrant upsert / delete
    # ------------------------------------------------------------------

    async def upsert_chunks(
        self,
        chunks: list[CodeChunk],
        repo_id: int,
        commit_sha: str,
    ) -> EmbedStats:
        """Embed chunks and upsert them into the code_chunks collection.

        Skips chunks with empty content.  Returns statistics for cost tracking.
        """
        stats = EmbedStats()
        if not chunks:
            return stats

        # Filter out empty chunks
        valid_chunks = [c for c in chunks if c.content.strip()]
        if not valid_chunks:
            return stats

        try:
            vectors = await self.embed_batch([c.content for c in valid_chunks])
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"Batch embedding failed: {exc}") from exc

        stats.cache_misses = len(valid_chunks)  # approximate; full tracking in embed_batch
        stats.chunks_upserted = len(valid_chunks)

        # Build PointStruct list
        points: list[qdrant_models.PointStruct] = []
        for chunk, vector in zip(valid_chunks, vectors):
            points.append(
                qdrant_models.PointStruct(
                    id=str(_chunk_id_to_uuid(chunk.chunk_id)),
                    vector=vector,
                    payload={
                        "repo_id": repo_id,
                        "file_path": chunk.file_path,
                        "chunk_id": chunk.chunk_id,
                        "chunk_type": chunk.chunk_type,
                        "name": chunk.name,
                        "language": chunk.language,
                        "start_line": chunk.start_line,
                        "end_line": chunk.end_line,
                        "commit_sha": commit_sha,
                        "content": chunk.content,
                    },
                )
            )

        # Upsert in batches
        try:
            for batch_start in range(0, len(points), QDRANT_UPSERT_BATCH):
                batch = points[batch_start : batch_start + QDRANT_UPSERT_BATCH]
                await self._qdrant.upsert(
                    collection_name=QDRANT_COLLECTION,
                    points=batch,
                )
        except Exception as exc:
            raise QdrantError(f"Qdrant upsert failed: {exc}") from exc

        logger.debug(
            "Upserted %d chunks to Qdrant (repo_id=%d, sha=%s)",
            len(valid_chunks),
            repo_id,
            commit_sha[:8],
        )
        return stats

    async def upsert_points_to_collection(
        self,
        collection_name: str,
        points: list[qdrant_models.PointStruct],
    ) -> None:
        """Generic upsert to any collection (used for past_findings)."""
        try:
            for batch_start in range(0, len(points), QDRANT_UPSERT_BATCH):
                batch = points[batch_start : batch_start + QDRANT_UPSERT_BATCH]
                await self._qdrant.upsert(
                    collection_name=collection_name,
                    points=batch,
                )
        except Exception as exc:
            raise QdrantError(f"Qdrant upsert to '{collection_name}' failed: {exc}") from exc

    async def delete_repo_chunks(self, repo_id: int) -> int:
        """Delete all vectors for a repository from code_chunks.

        Returns the approximate number of deleted points (Qdrant does not
        always report exact counts).
        """
        try:
            result = await self._qdrant.delete(
                collection_name=QDRANT_COLLECTION,
                points_selector=qdrant_models.FilterSelector(
                    filter=qdrant_models.Filter(
                        must=[
                            qdrant_models.FieldCondition(
                                key="repo_id",
                                match=qdrant_models.MatchValue(value=repo_id),
                            )
                        ]
                    )
                ),
            )
            count = getattr(result, "deleted_count", 0) or 0
            logger.info("Deleted ~%d chunks for repo_id=%d", count, repo_id)
            return count
        except Exception as exc:
            raise QdrantError(f"Qdrant delete failed for repo_id={repo_id}") from exc

    async def delete_file_chunks(self, repo_id: int, file_path: str) -> None:
        """Delete all vectors for a specific file in a repository."""
        try:
            await self._qdrant.delete(
                collection_name=QDRANT_COLLECTION,
                points_selector=qdrant_models.FilterSelector(
                    filter=qdrant_models.Filter(
                        must=[
                            qdrant_models.FieldCondition(
                                key="repo_id",
                                match=qdrant_models.MatchValue(value=repo_id),
                            ),
                            qdrant_models.FieldCondition(
                                key="file_path",
                                match=qdrant_models.MatchValue(value=file_path),
                            ),
                        ]
                    )
                ),
            )
        except Exception as exc:
            raise QdrantError(
                f"Qdrant delete failed for repo_id={repo_id}, file={file_path}"
            ) from exc

    async def scroll_chunk_ids_for_file(
        self, repo_id: int, file_path: str, commit_sha: str
    ) -> set[str]:
        """Return set of chunk_ids already indexed at the given commit_sha for a file."""
        try:
            results, _ = await self._qdrant.scroll(
                collection_name=QDRANT_COLLECTION,
                scroll_filter=qdrant_models.Filter(
                    must=[
                        qdrant_models.FieldCondition(
                            key="repo_id",
                            match=qdrant_models.MatchValue(value=repo_id),
                        ),
                        qdrant_models.FieldCondition(
                            key="file_path",
                            match=qdrant_models.MatchValue(value=file_path),
                        ),
                        qdrant_models.FieldCondition(
                            key="commit_sha",
                            match=qdrant_models.MatchValue(value=commit_sha),
                        ),
                    ]
                ),
                with_payload=True,
                limit=1000,
            )
            return {str(p.payload.get("chunk_id", "")) for p in results if p.payload}
        except Exception:
            logger.debug(
                "Could not scroll chunk IDs for %s@%s — assuming none indexed",
                file_path,
                commit_sha[:8],
            )
            return set()

    async def search(
        self,
        query_vector: list[float],
        collection_name: str,
        search_filter: qdrant_models.Filter | None,
        limit: int,
    ) -> list[qdrant_models.ScoredPoint]:
        """Perform a vector similarity search."""
        try:
            return await self._qdrant.search(
                collection_name=collection_name,
                query_vector=query_vector,
                query_filter=search_filter,
                limit=limit,
                with_payload=True,
            )
        except Exception as exc:
            raise QdrantError(f"Qdrant search failed: {exc}") from exc

    async def scroll(
        self,
        collection_name: str,
        scroll_filter: qdrant_models.Filter,
        limit: int = 100,
    ) -> list[qdrant_models.Record]:
        """Scroll records matching a filter (keyword / payload search)."""
        try:
            results, _ = await self._qdrant.scroll(
                collection_name=collection_name,
                scroll_filter=scroll_filter,
                with_payload=True,
                limit=limit,
            )
            return list(results)
        except Exception as exc:
            raise QdrantError(f"Qdrant scroll failed: {exc}") from exc

    # ------------------------------------------------------------------
    #  Lifecycle
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """Close the async Qdrant and HTTP clients."""
        try:
            await self._qdrant.close()
        except Exception:
            pass
        try:
            await self._hf_client.aclose()
        except Exception:
            pass
