# Semantic chunker for the RAG ingestion pipeline.
# NOT a fixed-size splitter. Chunks on paragraph/section boundaries.
# Target chunk size: 512–1024 tokens (measured with tiktoken).
# Each chunk retains: source metadata, section heading, chunk index, content hash.
# Content hash enables deduplication in the indexer.

# TODO: implement semantic chunker with tiktoken token budgeting in Phase 6 (RAG layer)
