"""
Hybrid retriever — dense vector search + sparse keyword search, merged with
Reciprocal Rank Fusion (RRF).

Algorithm:
    1. Dense: embed query → cosine similarity search via pgvector ARRAY comparison
    2. Sparse: PostgreSQL tsvector/pg_trgm full-text search on chunk_text
    3. Merge: RRF score = 1/(rank_dense + 60) + 1/(rank_sparse + 60)
    4. Sort by RRF score descending, return top 30 candidates for reranking
    5. Optionally filter by doc_type for collection-scoped queries

RRF smoothing constant 60 is from the original Cormack et al. paper (2009)
and is widely used in production RAG systems.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import structlog
from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from services.rag.ingestion.embedder import embed_query
from shared.config.settings import get_settings
from shared.db.models import KnowledgeEmbedding

logger = structlog.get_logger(__name__)

_RRF_K = 60          # RRF smoothing constant
_DENSE_TOP_N = 20    # Candidates from dense search
_SPARSE_TOP_N = 20   # Candidates from sparse search
_FINAL_TOP_N = 30    # Merged results to pass to reranker


@dataclass
class RetrievalResult:
    """A single retrieved chunk with its scores."""
    chunk_id: str
    doc_type: str
    source_file: str
    chunk_index: int
    chunk_text: str
    dense_rank: Optional[int] = None
    sparse_rank: Optional[int] = None
    rrf_score: float = 0.0
    content_hash: str = ""


async def retrieve(
    query: str,
    db: AsyncSession,
    doc_type_filter: Optional[str] = None,
    top_n: int = _FINAL_TOP_N,
) -> list[RetrievalResult]:
    """
    Hybrid retrieval: dense + sparse → RRF merge.

    Args:
        query: Natural language query string
        db: Async SQLAlchemy session
        doc_type_filter: Optional collection filter (e.g., "product_catalog")
        top_n: Number of candidates to return (before reranking)

    Returns:
        List of RetrievalResult sorted by RRF score descending
    """
    settings = get_settings()
    similarity_threshold = settings.RAG_SIMILARITY_THRESHOLD

    # Embed query for dense search
    query_embedding = await embed_query(query)

    # Run dense and sparse searches sequentially to avoid concurrent DB session access
    dense_results = await _dense_search(db, query_embedding, doc_type_filter, similarity_threshold)
    sparse_results = await _sparse_search(db, query, doc_type_filter)

    # RRF merge
    merged = _reciprocal_rank_fusion(dense_results, sparse_results)
    return merged[:top_n]


async def _dense_search(
    db: AsyncSession,
    query_embedding: list[float],
    doc_type_filter: Optional[str],
    similarity_threshold: float,
) -> list[RetrievalResult]:
    """
    pgvector cosine similarity search.

    Note: We store embeddings as ARRAY(Float) for SQLAlchemy ORM compatibility.
    The cosine similarity is computed by casting to the vector type at query time.
    In production, migrate the column to VECTOR(1536) for native HNSW support.
    """
    try:
        # Build embedding literal for SQL — cast ARRAY to vector
        embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

        conditions = ["embedding IS NOT NULL"]
        params: dict = {"embedding_str": embedding_str, "limit": _DENSE_TOP_N}

        if doc_type_filter:
            conditions.append("doc_type = :doc_type")
            params["doc_type"] = doc_type_filter

        where_clause = " AND ".join(conditions)

        # Using pgvector cosine distance: 1 - cosine_distance = cosine_similarity
        sql = text(f"""
            SELECT
                CAST(id AS text) as chunk_id,
                doc_type,
                source_file,
                chunk_index,
                chunk_text,
                content_hash,
                1 - (CAST(embedding AS vector(1536)) <=> CAST(:embedding_str AS vector(1536))) as similarity
            FROM knowledge_embeddings
            WHERE {where_clause}
                AND 1 - (CAST(embedding AS vector(1536)) <=> CAST(:embedding_str AS vector(1536))) >= {similarity_threshold}
            ORDER BY similarity DESC
            LIMIT :limit
        """)

        result = await db.execute(sql, params)
        rows = result.fetchall()

        return [
            RetrievalResult(
                chunk_id=row.chunk_id,
                doc_type=row.doc_type,
                source_file=row.source_file,
                chunk_index=row.chunk_index,
                chunk_text=row.chunk_text,
                content_hash=row.content_hash,
                dense_rank=rank + 1,
            )
            for rank, row in enumerate(rows)
        ]
    except Exception as exc:
        logger.error("dense_search_failed", error=str(exc))
        # Graceful degradation — return empty, sparse search still runs
        return []


async def _sparse_search(
    db: AsyncSession,
    query: str,
    doc_type_filter: Optional[str],
) -> list[RetrievalResult]:
    """
    PostgreSQL full-text search using pg_trgm similarity on chunk_text.
    Returns up to _SPARSE_TOP_N results ordered by trigram similarity.
    """
    try:
        params: dict = {"query": query, "limit": _SPARSE_TOP_N}
        conditions = ["similarity(chunk_text, :query) > 0.05"]

        if doc_type_filter:
            conditions.append("doc_type = :doc_type")
            params["doc_type"] = doc_type_filter

        where_clause = " AND ".join(conditions)

        sql = text(f"""
            SELECT
                CAST(id AS text) as chunk_id,
                doc_type,
                source_file,
                chunk_index,
                chunk_text,
                content_hash,
                similarity(chunk_text, :query) as sim_score
            FROM knowledge_embeddings
            WHERE {where_clause}
            ORDER BY sim_score DESC
            LIMIT :limit
        """)

        result = await db.execute(sql, params)
        rows = result.fetchall()

        return [
            RetrievalResult(
                chunk_id=row.chunk_id,
                doc_type=row.doc_type,
                source_file=row.source_file,
                chunk_index=row.chunk_index,
                chunk_text=row.chunk_text,
                content_hash=row.content_hash,
                sparse_rank=rank + 1,
            )
            for rank, row in enumerate(rows)
        ]
    except Exception as exc:
        logger.error("sparse_search_failed", error=str(exc))
        return []


def _reciprocal_rank_fusion(
    dense: list[RetrievalResult],
    sparse: list[RetrievalResult],
) -> list[RetrievalResult]:
    """
    Merge dense and sparse results using RRF.
    RRF score = 1/(rank + k) for each list where the document appears.
    Documents in both lists get score from both; exclusive to one list get score from one.
    """
    scores: dict[str, float] = {}
    merged: dict[str, RetrievalResult] = {}

    # Score from dense results
    for result in dense:
        key = result.chunk_id
        rrf = 1.0 / (result.dense_rank + _RRF_K)
        scores[key] = scores.get(key, 0.0) + rrf
        merged[key] = result

    # Score from sparse results
    for result in sparse:
        key = result.chunk_id
        rrf = 1.0 / (result.sparse_rank + _RRF_K)
        scores[key] = scores.get(key, 0.0) + rrf
        if key in merged:
            # Update sparse rank on existing result
            merged[key].sparse_rank = result.sparse_rank
        else:
            merged[key] = result

    # Attach RRF scores and sort
    for key, result in merged.items():
        result.rrf_score = scores[key]

    sorted_results = sorted(merged.values(), key=lambda r: r.rrf_score, reverse=True)

    logger.debug(
        "rrf_merge_complete",
        dense_count=len(dense),
        sparse_count=len(sparse),
        merged_count=len(sorted_results),
    )

    return sorted_results
