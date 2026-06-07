"""
Embedding Sync Task — triggered manually or by file upload.

Runs the full RAG ingestion pipeline (loader → chunker → embedder → indexer)
on a specified directory or file path. Idempotent — safe to run multiple times
because the indexer uses SHA256 content hashing to skip unchanged chunks.

Triggered via:
    from services.workers.tasks.embedding_sync import sync_embeddings
    sync_embeddings.delay(path="services/rag/knowledge_base")   # Full backfill
    sync_embeddings.delay(path="services/rag/knowledge_base/product_catalog/personal_loan_eligibility.md")
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import structlog

from services.workers.celery_app import app

logger = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()


@app.task(
    name="services.workers.tasks.embedding_sync.sync_embeddings",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=600,
    time_limit=720,
    acks_late=True,
)
def sync_embeddings(self, path: str = None):
    """
    Celery task entry point.

    Args:
        path: Directory or file path to ingest. If None, ingests the full
              knowledge_base directory.
    """
    try:
        target = path or str(REPO_ROOT / "services" / "rag" / "knowledge_base")
        return asyncio.run(_sync_async(target))
    except Exception as exc:
        logger.error("embedding_sync_failed", path=path, error=str(exc))
        raise self.retry(exc=exc)


async def _sync_async(target_path: str) -> dict:
    """Async implementation of embedding sync."""
    from services.rag.ingestion.loader import load_knowledge_base, LoadedDocument
    from services.rag.ingestion.chunker import chunk_documents
    from services.rag.ingestion.embedder import embed_chunks
    from services.rag.ingestion.indexer import index_embeddings
    from shared.db.session import get_async_session

    target = Path(target_path)
    logger.info("embedding_sync_started", path=str(target))

    # Determine if path is a file or directory
    if target.is_file() and target.suffix == ".md":
        # Single file — wrap in pseudo-loader
        documents = _load_single_file(target)
    elif target.is_dir():
        documents = load_knowledge_base(target)
    else:
        raise ValueError(f"Path must be a .md file or knowledge_base directory: {target}")

    if not documents:
        logger.warning("no_documents_found", path=str(target))
        return {"documents": 0, "chunks": 0, "new": 0, "skipped": 0}

    # Chunk
    chunks = chunk_documents(documents)
    logger.info("chunks_created", count=len(chunks))

    # Embed
    embedded = await embed_chunks(chunks)
    logger.info("embeddings_generated", count=len(embedded))

    # Index
    async for session in get_async_session():
        result = await index_embeddings(session, embedded)
        break

    summary = {
        "documents": len(documents),
        "chunks": len(chunks),
        "new": result.new_chunks,
        "skipped": result.skipped_chunks,
        "failed": result.failed_chunks,
    }
    logger.info("embedding_sync_complete", **summary)
    return summary


def _load_single_file(md_file: Path) -> list:
    """Load a single markdown file as a LoadedDocument."""
    from services.rag.ingestion.loader import _load_single_file as loader_fn, _DIR_TO_DOC_TYPE

    # Determine doc_type from parent directory name
    parent_name = md_file.parent.name
    doc_type = _DIR_TO_DOC_TYPE.get(parent_name, "product_catalog")

    # Use the kb root as the parent of the parent
    kb_root = md_file.parent.parent
    return [loader_fn(md_file, doc_type, kb_root)]
