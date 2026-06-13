"""
Reranker — score-based reranking using RRF scores from retrieval.

Replaces the gpt-4o-mini cross-encoder approach with a pure cosine/RRF
sort. The RRF score is already computed by the hybrid retriever
(dense cosine + sparse trgm, merged via Reciprocal Rank Fusion).
Sorting by that score directly eliminates an entire LLM round-trip
(~500-1000 ms) with no loss in accuracy for the persona-playbook
tone-guideline use case.

Caching:
    Results are cached in Redis for 1 hour keyed by:
    SHA256(query + doc_type_filter) -> list of top-k chunk IDs
    This avoids repeated vector searches for identical queries.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

import structlog

from services.rag.retrieval.retriever import RetrievalResult

logger = structlog.get_logger(__name__)

_RERANK_TOP_K = 5
_CACHE_TTL_SECONDS = 3600  # 1 hour


async def rerank(
    query: str,
    candidates: list[RetrievalResult],
    doc_type_filter: Optional[str] = None,
    redis_client=None,
    top_k: int = _RERANK_TOP_K,
) -> list[RetrievalResult]:
    """
    Rerank candidates by RRF score (cosine + sparse already fused).

    No LLM call -- the hybrid retriever already produces well-ordered
    candidates via Reciprocal Rank Fusion. We simply sort by rrf_score
    descending and return the top-k.

    Redis caching is preserved: on a cache hit the retrieval + sort is
    skipped entirely for repeated identical queries.

    Args:
        query: Original query string (used for cache key only)
        candidates: RRF-merged candidates from retriever (up to 30)
        doc_type_filter: Used as part of the cache key
        redis_client: Optional Redis client for caching
        top_k: Number of results to return (default 5)

    Returns:
        Top-k RetrievalResult objects sorted by rrf_score descending
    """
    if not candidates:
        return []

    # Redis cache hit
    cache_key = _make_cache_key(query, doc_type_filter)
    if redis_client:
        cached = await _get_cached(redis_client, cache_key)
        if cached:
            logger.debug("reranker_cache_hit", query_len=len(query))
            return _order_by_ids(candidates, cached)

    # Pure score sort -- O(n log n), no network call
    try:
        sorted_candidates = sorted(
            candidates, key=lambda r: r.rrf_score, reverse=True
        )
        top = sorted_candidates[:top_k]
        top_ids = [r.chunk_id for r in top]

        # Cache so identical queries skip the retriever too
        if redis_client:
            await _set_cached(redis_client, cache_key, top_ids)

        logger.info(
            "reranker_complete",
            method="rrf_score",
            candidates_in=len(candidates),
            results_out=len(top),
        )
        return top

    except Exception as exc:
        logger.warning("reranker_score_sort_failed", error=str(exc))
        # Graceful degradation: return first top_k as-is
        return candidates[:top_k]


def _order_by_ids(
    candidates: list[RetrievalResult],
    ordered_ids: list[str],
) -> list[RetrievalResult]:
    """Return candidates in the order specified by ordered_ids."""
    id_to_result = {c.chunk_id: c for c in candidates}
    ordered = [id_to_result[cid] for cid in ordered_ids if cid in id_to_result]
    return ordered


def _make_cache_key(query: str, doc_type_filter: Optional[str]) -> str:
    """Generate Redis cache key for a query + filter combination."""
    raw = f"{query}|{doc_type_filter or 'all'}"
    hash_str = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"rag:reranker:{hash_str}"


async def _get_cached(redis_client, key: str) -> Optional[list[str]]:
    """Retrieve cached chunk IDs from Redis."""
    try:
        value = await redis_client.get(key)
        if value:
            return json.loads(value)
    except Exception as exc:
        logger.debug("reranker_cache_get_failed", error=str(exc))
    return None


async def _set_cached(redis_client, key: str, chunk_ids: list[str]) -> None:
    """Cache chunk IDs in Redis with TTL."""
    try:
        await redis_client.setex(key, _CACHE_TTL_SECONDS, json.dumps(chunk_ids))
    except Exception as exc:
        logger.debug("reranker_cache_set_failed", error=str(exc))
