"""Dependency wiring for the RAG components.

Provides factory functions that wire together EmbeddingService,
ContextRetriever, and ContextBuilder with the application settings.

Import from here rather than constructing components directly to avoid
circular imports and to make testing easier via dependency injection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.rag.context_builder import ContextBuilder
from app.rag.embedder import EmbeddingService
from app.rag.retriever import ContextRetriever

if TYPE_CHECKING:
    from app.config import Settings
    from app.llm.client import LLMClient


def create_embedding_service(redis: Any, settings: "Settings") -> EmbeddingService:
    """Create an ``EmbeddingService`` from application settings."""
    return EmbeddingService(
        redis=redis,
        qdrant_url=settings.qdrant_url,
        openai_api_key=settings.openai_api_key,
    )


def create_context_builder(
    redis: Any,
    llm_client: "LLMClient",
    settings: "Settings",
) -> ContextBuilder:
    """Create a fully-wired ``ContextBuilder`` from application settings."""
    embedder = create_embedding_service(redis, settings)
    retriever = ContextRetriever(embedder)
    return ContextBuilder(retriever=retriever, llm_client=llm_client, redis=redis)
