# pgvector indexer for the RAG ingestion pipeline.
# Upserts embedded chunks into the knowledge_embeddings table.
# Uses HNSW index for approximate nearest neighbour search.
# Deduplication: chunks with matching content_hash are skipped (not re-embedded).
# Soft-deletes previous versions of updated documents before inserting new chunks.

# TODO: implement pgvector upsert with HNSW indexing in Phase 6 (RAG layer)
