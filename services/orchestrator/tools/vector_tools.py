"""
Vector tools — RAG retrieval via pgvector hybrid search.

Phase 5 will implement full vector embeddings and HNSW indexing.
This stub returns an empty result list so agents degrade gracefully.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


async def hybrid_search(
    query: str,
    collection: str,
    top_k: int = 5,
    alpha: float = 0.7,  # vector weight vs. BM25 weight
) -> list[dict]:
    """
    Hybrid search combining vector similarity and keyword BM25.

    Args:
        query: Natural language query string
        collection: Knowledge base collection name
                    (product_catalog, policy_docs, persona_playbooks, market_context)
        top_k: Number of results to return
        alpha: Weight for vector search (1-alpha goes to keyword search)

    Returns:
        List of dicts with keys: id, content, score, metadata

    Note: Phase 5 implements this using pgvector + tsvector full-text search.
    """
    logger.debug("vector_search_called", collection=collection, query_length=len(query))
    # Phase 5: implement vector search against pgvector
    # For now, return empty list — agents have fallbacks for empty RAG results
    return []
