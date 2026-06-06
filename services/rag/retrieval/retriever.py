# Hybrid retriever combining dense vector search and sparse keyword search.
# Dense: pgvector cosine similarity (top-20 candidates).
# Sparse: BM25 over chunk_text using rank_bm25 (top-20 candidates).
# Merge: Reciprocal Rank Fusion (RRF) → top-30 candidates passed to reranker.
# Filters by doc_type when the agent specifies a retrieval domain.

# TODO: implement hybrid retriever with RRF fusion in Phase 6 (RAG layer)
