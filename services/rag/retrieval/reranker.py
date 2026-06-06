"""
Reranker — cross-encoder reranking using gpt-4o-mini.

Takes top-30 RRF-fused candidates and asks gpt-4o-mini to return the
top-5 most relevant chunk IDs in JSON format. This is cheaper than a
dedicated cross-encoder model and works well for this use case.

Caching:
    Reranker results are cached in Redis for 1 hour keyed by:
    SHA256(query + doc_type_filter) → list of top-5 chunk IDs

    This avoids repeated LLM calls for identical queries within the cache window.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

import structlog
from pydantic import BaseModel, Field

from services.rag.retrieval.retriever import RetrievalResult

logger = structlog.get_logger(__name__)

_RERANK_TOP_K = 5
_CACHE_TTL_SECONDS = 3600  # 1 hour


class RerankerOutput(BaseModel):
    """Expected JSON output from the reranker LLM call."""
    top_chunk_ids: list[str] = Field(
        ...,
        description="Ordered list of top chunk IDs, most relevant first"
    )


async def rerank(
    query: str,
    candidates: list[RetrievalResult],
    doc_type_filter: Optional[str] = None,
    redis_client=None,
    top_k: int = _RERANK_TOP_K,
) -> list[RetrievalResult]:
    """
    Rerank candidates using gpt-4o-mini cross-encoder approach.

    Args:
        query: Original query string
        candidates: RRF-merged candidates from retriever (up to 30)
        doc_type_filter: Used as part of the cache key
        redis_client: Optional Redis client for caching
        top_k: Number of results to return (default 5)

    Returns:
        Top-k RetrievalResult objects in reranked order
    """
    if not candidates:
        return []

    # Check Redis cache
    cache_key = _make_cache_key(query, doc_type_filter)
    if redis_client:
        cached = await _get_cached(redis_client, cache_key)
        if cached:
            logger.debug("reranker_cache_hit", query_len=len(query))
            return _order_by_ids(candidates, cached)

    # Build reranking prompt
    candidates_text = _format_candidates(candidates)
    prompt = f"""You are a relevance ranking assistant for a banking knowledge base.

Query: "{query}"

Below are {len(candidates)} document chunks. Return the IDs of the top {top_k} chunks that best answer the query, ordered from most to least relevant.

Document Chunks:
{candidates_text}

Return JSON only, in this format:
{{"top_chunk_ids": ["id1", "id2", "id3", "id4", "id5"]}}

Rules:
- Return exactly {top_k} IDs (or fewer if less than {top_k} chunks are relevant)
- Order by relevance — most relevant first
- Only include IDs from the list above
- No explanation, just the JSON"""

    try:
        from services.orchestrator.llm.router import get_llm_router
        raw_output = await get_llm_router().call_fast(
            prompt=prompt,
            system="You are a precise relevance ranking assistant. Return only valid JSON.",
            temperature=0.0,
        )

        # Parse the response
        reranker_output = RerankerOutput.model_validate(
            json.loads(raw_output.strip().strip("```json").strip("```"))
        )
        top_ids = reranker_output.top_chunk_ids[:top_k]

        # Cache the result
        if redis_client:
            await _set_cached(redis_client, cache_key, top_ids)

        result = _order_by_ids(candidates, top_ids)
        logger.info(
            "reranker_complete",
            candidates_in=len(candidates),
            results_out=len(result),
        )
        return result

    except Exception as exc:
        logger.warning(
            "reranker_failed_falling_back_to_rrf",
            error=str(exc),
        )
        # Graceful degradation: return RRF-sorted top-k
        return candidates[:top_k]


def _format_candidates(candidates: list[RetrievalResult]) -> str:
    """Format candidates as numbered list for the reranker prompt."""
    lines = []
    for i, c in enumerate(candidates):
        excerpt = c.chunk_text[:300].replace("\n", " ").strip()
        source = c.source_file.split("/")[-1].replace(".md", "")
        lines.append(
            f'ID: "{c.chunk_id}"\n'
            f'Source: {source} ({c.doc_type})\n'
            f'Content: {excerpt}...\n'
        )
    return "\n---\n".join(lines)


def _order_by_ids(
    candidates: list[RetrievalResult],
    ordered_ids: list[str],
) -> list[RetrievalResult]:
    """Return candidates in the order specified by ordered_ids."""
    id_to_result = {c.chunk_id: c for c in candidates}
    ordered = []
    for chunk_id in ordered_ids:
        if chunk_id in id_to_result:
            ordered.append(id_to_result[chunk_id])
    # Append any remaining (shouldn't happen with valid LLM output)
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
