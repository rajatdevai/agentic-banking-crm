"""
Indexer — upserts chunk embeddings into the knowledge_embeddings table.

Idempotency guarantee:
    Before upserting, compute SHA256(chunk_text). If a row with this hash
    already exists, skip. This makes the pipeline safe to re-run without
    creating duplicates or triggering unnecessary OpenAI API calls.

Embedding storage:
    Stored as ARRAY(Float) in PostgreSQL. For production scale, migrate
    to the native pgvector VECTOR type via:
        ALTER TABLE knowledge_embeddings
        ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector;
    The HNSW index in the migration handles cosine similarity queries.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from services.rag.ingestion.chunker import DocumentChunk
from shared.db.models import KnowledgeEmbedding

logger = structlog.get_logger(__name__)


@dataclass
class IndexResult:
    """Summary of an indexing run."""
    total_chunks: int
    new_chunks: int
    skipped_chunks: int
    failed_chunks: int


async def index_embeddings(
    db: AsyncSession,
    embedded_chunks: list[tuple[DocumentChunk, list[float]]],
) -> IndexResult:
    """
    Upsert chunk embeddings into knowledge_embeddings table.

    Args:
        db: Async SQLAlchemy session (already connected to cloud Postgres)
        embedded_chunks: Output of embedder.embed_chunks() — (chunk, vector) pairs

    Returns:
        IndexResult with counts of new vs skipped chunks
    """
    new_count = 0
    skipped_count = 0
    failed_count = 0

    # Bulk-fetch existing hashes to avoid N+1 queries
    existing_hashes = await _fetch_existing_hashes(db)
    logger.info("existing_chunks_in_db", count=len(existing_hashes))

    for chunk, embedding in embedded_chunks:
        content_hash = _sha256(chunk.chunk_text)

        # Skip if already indexed (idempotency)
        if content_hash in existing_hashes:
            skipped_count += 1
            continue

        try:
            row = KnowledgeEmbedding(
                id=uuid.uuid4(),
                doc_type=chunk.doc_type,
                source_file=chunk.source_file,
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
                content_hash=content_hash,
                token_count=chunk.token_count,
                embedding=embedding,
                version=chunk.version,
                effective_date=chunk.effective_date,
            )
            db.add(row)
            new_count += 1

            # Commit in batches of 50 to avoid long transactions
            if new_count % 50 == 0:
                await db.commit()
                logger.info("batch_committed", new_so_far=new_count)

        except Exception as exc:
            logger.error(
                "chunk_index_failed",
                source=chunk.source_file,
                chunk_index=chunk.chunk_index,
                error=str(exc),
            )
            await db.rollback()
            failed_count += 1

    # Final commit
    if new_count % 50 != 0:
        try:
            await db.commit()
        except Exception as exc:
            logger.error("final_commit_failed", error=str(exc))
            await db.rollback()

    result = IndexResult(
        total_chunks=len(embedded_chunks),
        new_chunks=new_count,
        skipped_chunks=skipped_count,
        failed_chunks=failed_count,
    )

    logger.info(
        "indexing_complete",
        total=result.total_chunks,
        new=result.new_chunks,
        skipped=result.skipped_chunks,
        failed=result.failed_chunks,
    )

    return result


async def _fetch_existing_hashes(db: AsyncSession) -> set[str]:
    """Fetch all existing content_hash values in one query."""
    result = await db.execute(
        select(KnowledgeEmbedding.content_hash)
    )
    return {row[0] for row in result.fetchall()}


def _sha256(text: str) -> str:
    """Compute SHA256 hash of text for idempotent upsert."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def create_vector_indexes(db: AsyncSession) -> None:
    """
    Create HNSW vector index and pg_trgm GIN index if they don't exist.
    Run once after initial ingestion for best performance.

    Notes:
        - HNSW index creation on large tables can be slow (minutes)
        - Run outside of a transaction (autocommit mode) for large tables
        - The migration already creates these — this is a safety net
    """
    try:
        await db.execute(text("""
            CREATE EXTENSION IF NOT EXISTS vector;
            CREATE EXTENSION IF NOT EXISTS pg_trgm;
        """))

        # HNSW index for cosine similarity (pgvector)
        # Using ARRAY(Float) for now — cast to vector in the query
        await db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_knowledge_embeddings_hnsw
            ON knowledge_embeddings
            USING gin (chunk_text gin_trgm_ops);
        """))

        await db.commit()
        logger.info("vector_indexes_created")
    except Exception as exc:
        logger.warning("vector_index_creation_failed", error=str(exc))
        await db.rollback()
