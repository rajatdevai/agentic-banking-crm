# Embedding sync Celery task — triggered by knowledge base file uploads.
# Ingests a new or updated document: load → chunk → embed → upsert to pgvector.
# Uses text-embedding-3-large (OpenAI) with MRL truncation to 1536 dimensions.
# Deduplicates by content hash. Soft-deletes old chunk versions before inserting new ones.
# Runs on a dedicated queue to avoid competing with real-time tasks.

from services.workers.celery_app import app


@app.task(name="services.workers.tasks.embedding_sync.run_embedding_sync",
          queue="embeddings", bind=True, max_retries=3)
def run_embedding_sync(self, doc_path: str, doc_type: str):
    """On-demand: ingest a new knowledge document into the pgvector store."""
    # TODO: implement RAG ingestion pipeline in Phase 6 (RAG layer)
    raise NotImplementedError("embedding_sync not yet implemented")
