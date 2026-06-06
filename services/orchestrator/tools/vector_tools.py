"""
Vector tools — the only RAG interface exposed to agents.

Agents NEVER import directly from services/rag/. They call this module.
This enforces a clean architectural boundary: the RAG internals (retriever,
reranker, context_builder) can be swapped without touching agent code.

Public API:
    search_knowledge_base(query, doc_type_filter, db, redis, top_k) → ContextResult
    hybrid_search(query, collection, top_k) → list[dict]  (backward-compat shim)

ContextResult dataclass:
    formatted_context: str     — ready for LLM injection
    source_citations: list     — [{source_file, doc_type, excerpt, rrf_score}]
    total_tokens: int
    chunks_included: int
    chunks_available: int
"""

from __future__ import annotations

from typing import Optional

import structlog

from services.rag.retrieval.context_builder import ContextResult, build_context
from services.rag.retrieval.reranker import rerank
from services.rag.retrieval.retriever import RetrievalResult, retrieve

logger = structlog.get_logger(__name__)


async def search_knowledge_base(
    query: str,
    db,
    doc_type_filter: Optional[str] = None,
    redis_client=None,
    top_k: int = 5,
    max_context_tokens: int = 3000,
) -> ContextResult:
    """
    Full RAG pipeline: retrieve → rerank → build context.

    This is the primary function agents should call. It orchestrates:
        1. retriever.retrieve() — hybrid dense + sparse search
        2. reranker.rerank() — gpt-4o-mini cross-encoder reranking
        3. context_builder.build_context() — token-budgeted formatting

    Args:
        query: Free-text query from the agent
        db: AsyncSession — required for DB access
        doc_type_filter: Optional collection filter for scoped search
                         Values: "product_catalog" | "policy_docs" |
                                 "persona_playbooks" | "market_context"
        redis_client: Optional Redis for reranker caching
        top_k: Number of chunks to include in context (default 5)
        max_context_tokens: Token budget for context block (default 3000)

    Returns:
        ContextResult with formatted_context ready for LLM prompt injection
    """
    if db is None:
        logger.warning("search_knowledge_base_called_without_db")
        return _empty_context_result()

    try:
        # Step 1: Hybrid retrieval (dense + sparse → RRF)
        candidates: list[RetrievalResult] = await retrieve(
            query=query,
            db=db,
            doc_type_filter=doc_type_filter,
        )

        if not candidates:
            logger.info("no_retrieval_candidates", query_len=len(query), filter=doc_type_filter)
            return _empty_context_result()

        # Step 2: Rerank with gpt-4o-mini
        top_chunks = await rerank(
            query=query,
            candidates=candidates,
            doc_type_filter=doc_type_filter,
            redis_client=redis_client,
            top_k=top_k,
        )

        # Step 3: Build context block
        context_result = build_context(
            query=query,
            reranked_chunks=top_chunks,
            max_context_tokens=max_context_tokens,
        )

        logger.info(
            "rag_search_complete",
            query_len=len(query),
            filter=doc_type_filter,
            candidates=len(candidates),
            included=context_result.chunks_included,
            tokens=context_result.total_tokens,
        )
        return context_result

    except Exception as exc:
        logger.error("rag_pipeline_error", error=str(exc), query=query[:100])
        return _empty_context_result()


async def hybrid_search(
    query: str,
    collection: str,
    top_k: int = 5,
    db=None,
    redis_client=None,
) -> list[dict]:
    """
    Backward-compatible shim for agents that call hybrid_search() directly.
    Returns a list of dicts instead of ContextResult.

    Used by Phase 4 agents written before ContextResult was introduced.
    Prefer search_knowledge_base() for new code.
    """
    context = await search_knowledge_base(
        query=query,
        db=db,
        doc_type_filter=collection,
        redis_client=redis_client,
        top_k=top_k,
    )

    # Convert citations to the dict format agents expect
    return [
        {
            "id": c.get("source_file", "") + f"_{c.get('chunk_index', 0)}",
            "content": c.get("excerpt", ""),
            "score": c.get("rrf_score", 0.0),
            "doc_type": c.get("doc_type", ""),
            "source": c.get("source_file", ""),
        }
        for c in context.source_citations
    ]


def _empty_context_result() -> ContextResult:
    """Return an empty ContextResult for error/no-data cases."""
    return ContextResult(
        formatted_context="No relevant context found in the knowledge base.",
        source_citations=[],
        total_tokens=0,
        chunks_included=0,
        chunks_available=0,
    )
