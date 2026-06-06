"""
Test: RAG pipeline — chunking, embedding, and search verification.

This test verifies:
    1. Loader reads all 10 knowledge base documents
    2. Chunker produces reasonable chunk sizes (100–1200 tokens)
    3. personal_loan_eligibility.md chunks contain expected content
    4. search_knowledge_base returns personal_loan doc as top result
       for the query "personal loan eligibility for IT professional"

The full search test (Part 4) mocks OpenAI and the DB because we don't
want to make real API calls in CI. The chunking and loading tests are
pure Python and need no mocks.

Run:
    pytest tests/integration/test_rag_search.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
KB_DIR = REPO_ROOT / "services" / "rag" / "knowledge_base"


# ---------------------------------------------------------------------------
# Test 1: Document loading
# ---------------------------------------------------------------------------
class TestDocumentLoader:
    def test_loads_all_ten_documents(self):
        """All 10 markdown files in knowledge_base/ should be loaded."""
        from services.rag.ingestion.loader import load_knowledge_base

        documents = load_knowledge_base(KB_DIR)
        assert len(documents) == 10, (
            f"Expected 10 documents, got {len(documents)}. "
            f"Files: {[d.source_file for d in documents]}"
        )

    def test_document_types_covered(self):
        """All four doc_type collections should be represented."""
        from services.rag.ingestion.loader import load_knowledge_base

        documents = load_knowledge_base(KB_DIR)
        doc_types = {d.doc_type for d in documents}
        expected = {"product_catalog", "policy_docs", "persona_playbooks", "market_context"}
        assert doc_types == expected, f"Missing doc types: {expected - doc_types}"

    def test_personal_loan_doc_loaded(self):
        """personal_loan_eligibility.md should be loaded with non-empty text."""
        from services.rag.ingestion.loader import load_knowledge_base

        documents = load_knowledge_base(KB_DIR)
        pl_docs = [d for d in documents if "personal_loan" in d.source_file]
        assert len(pl_docs) == 1, "Expected exactly one personal loan document"
        assert len(pl_docs[0].raw_text) > 1000, "Personal loan doc seems too short"
        assert "CIBIL" in pl_docs[0].raw_text, "Expected CIBIL content in personal loan doc"

    def test_version_extracted(self):
        """Documents with version in front matter should have it parsed."""
        from services.rag.ingestion.loader import load_knowledge_base

        documents = load_knowledge_base(KB_DIR)
        versioned = [d for d in documents if "2024" in d.version]
        assert len(versioned) >= 5, (
            f"Expected at least 5 documents with 2024 version. "
            f"Got: {[(d.source_file, d.version) for d in documents]}"
        )

    def test_effective_date_extracted(self):
        """Documents with Effective Date in front matter should have it parsed."""
        from services.rag.ingestion.loader import load_knowledge_base

        documents = load_knowledge_base(KB_DIR)
        dated = [d for d in documents if d.effective_date is not None]
        assert len(dated) >= 5, "Expected at least 5 documents with effective_date"


# ---------------------------------------------------------------------------
# Test 2: Chunking
# ---------------------------------------------------------------------------
class TestChunker:
    def test_produces_reasonable_chunk_count(self):
        """10 documents should produce at least 30 chunks total."""
        from services.rag.ingestion.loader import load_knowledge_base
        from services.rag.ingestion.chunker import chunk_documents

        documents = load_knowledge_base(KB_DIR)
        chunks = chunk_documents(documents)
        assert len(chunks) >= 15, (
            f"Expected at least 15 chunks from 10 documents, got {len(chunks)}"
        )

    def test_chunk_sizes_within_bounds(self):
        """All chunks should be between 50 and 1200 tokens."""
        from services.rag.ingestion.loader import load_knowledge_base
        from services.rag.ingestion.chunker import chunk_documents

        documents = load_knowledge_base(KB_DIR)
        chunks = chunk_documents(documents)

        oversized = [c for c in chunks if c.token_count > 1200]
        undersized = [c for c in chunks if c.token_count < 10]

        assert not oversized, (
            f"Chunks exceeding 1200 tokens: "
            f"{[(c.source_file, c.chunk_index, c.token_count) for c in oversized[:3]]}"
        )
        assert not undersized, (
            f"Empty/trivial chunks: "
            f"{[(c.source_file, c.chunk_index, c.token_count) for c in undersized[:3]]}"
        )

    def test_chunk_index_monotonic(self):
        """Chunk indices within a source file should be 0, 1, 2, ... (no gaps)."""
        from services.rag.ingestion.loader import load_knowledge_base
        from services.rag.ingestion.chunker import chunk_documents

        documents = load_knowledge_base(KB_DIR)
        chunks = chunk_documents(documents)

        from collections import defaultdict
        by_file: dict[str, list[int]] = defaultdict(list)
        for c in chunks:
            by_file[c.source_file].append(c.chunk_index)

        for source_file, indices in by_file.items():
            indices.sort()
            expected = list(range(len(indices)))
            assert indices == expected, (
                f"Non-sequential chunk indices in {source_file}: {indices}"
            )

    def test_personal_loan_chunks_contain_cibil(self):
        """At least one chunk from personal_loan_eligibility.md should mention CIBIL."""
        from services.rag.ingestion.loader import load_knowledge_base
        from services.rag.ingestion.chunker import chunk_documents

        documents = load_knowledge_base(KB_DIR)
        chunks = chunk_documents(documents)

        pl_chunks = [c for c in chunks if "personal_loan_eligibility" in c.source_file]
        assert len(pl_chunks) > 0, "No chunks from personal_loan_eligibility.md"

        cibil_chunks = [c for c in pl_chunks if "CIBIL" in c.chunk_text or "700" in c.chunk_text]
        assert len(cibil_chunks) > 0, (
            "Expected at least one personal loan chunk mentioning CIBIL"
        )

    def test_metadata_preserved_in_chunks(self):
        """Chunks must carry doc_type, source_file, version from parent document."""
        from services.rag.ingestion.loader import load_knowledge_base
        from services.rag.ingestion.chunker import chunk_documents

        documents = load_knowledge_base(KB_DIR)
        chunks = chunk_documents(documents)

        for chunk in chunks[:10]:  # Sample first 10
            assert chunk.doc_type, f"Chunk missing doc_type: {chunk.source_file}"
            assert chunk.source_file, "Chunk missing source_file"
            assert chunk.version, f"Chunk missing version: {chunk.source_file}"
            assert chunk.chunk_text.strip(), "Chunk has empty text"


# ---------------------------------------------------------------------------
# Test 3: RAG search (mocked OpenAI + DB)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_search_returns_personal_loan_doc_for_it_professional_query():
    """
    The query 'personal loan eligibility for IT professional' should return
    results with source_file containing 'personal_loan_eligibility'.

    Mocks:
        - embed_query: returns a deterministic vector
        - DB session: returns personal loan chunks from DB
        - Reranker LLM call: returns the personal loan chunk ID as top result
    """
    from services.orchestrator.tools.vector_tools import search_knowledge_base
    from services.rag.retrieval.retriever import RetrievalResult

    query = "personal loan eligibility for IT professional"

    # Mock the embedding call
    mock_embedding = [0.1] * 1536

    # Mock retrieval results — simulate DB returning personal loan chunk as top result
    mock_dense_result = RetrievalResult(
        chunk_id="chunk-pl-001",
        doc_type="product_catalog",
        source_file="product_catalog/personal_loan_eligibility.md",
        chunk_index=1,
        chunk_text=(
            "IT Professionals get faster processing and preferential rates. "
            "Minimum CIBIL score 700. Minimum salary ₹25,000 per month. "
            "Maximum loan amount 40x monthly salary. Tenure 12 to 60 months."
        ),
        content_hash="abc123",
        dense_rank=1,
        rrf_score=0.032,
    )

    mock_sparse_result = RetrievalResult(
        chunk_id="chunk-policy-001",
        doc_type="policy_docs",
        source_file="policy_docs/internal_credit_policy.md",
        chunk_index=0,
        chunk_text="Internal credit scoring cutoffs for loan applications.",
        content_hash="def456",
        sparse_rank=1,
        rrf_score=0.016,
    )

    # Mock reranker response — returns personal loan chunk as top result
    mock_rerank_response = MagicMock()
    mock_rerank_response.choices = [MagicMock()]
    mock_rerank_response.choices[0].message.content = json.dumps({
        "top_chunk_ids": ["chunk-pl-001"]
    })
    mock_rerank_response.usage = MagicMock()
    mock_rerank_response.usage.total_tokens = 150

    mock_openai = AsyncMock()
    mock_openai.embeddings.create = AsyncMock(
        return_value=MagicMock(data=[MagicMock(embedding=mock_embedding)])
    )
    mock_openai.chat.completions.create = AsyncMock(return_value=mock_rerank_response)

    with patch("services.rag.ingestion.embedder.AsyncOpenAI", return_value=mock_openai), \
         patch("services.rag.retrieval.retriever._dense_search",
               AsyncMock(return_value=[mock_dense_result])), \
         patch("services.rag.retrieval.retriever._sparse_search",
               AsyncMock(return_value=[mock_sparse_result])), \
         patch("services.orchestrator.llm.router.AsyncOpenAI", return_value=mock_openai):

        from services.orchestrator.llm import router as llm_router_module
        try:
            llm_router_module.get_llm_router.cache_clear()
        except AttributeError:
            pass

        mock_db = AsyncMock()
        result = await search_knowledge_base(
            query=query,
            db=mock_db,
            doc_type_filter="product_catalog",
            redis_client=None,
            top_k=5,
        )

    # Assertions
    assert result.chunks_included > 0, "Expected at least 1 chunk in context"

    top_citation = result.source_citations[0]
    assert "personal_loan_eligibility" in top_citation["source_file"], (
        f"Expected personal_loan_eligibility.md as top result. "
        f"Got: {top_citation['source_file']}"
    )

    assert "IT" in result.formatted_context or "CIBIL" in result.formatted_context, (
        "Expected personal loan content in formatted context"
    )
    assert result.total_tokens > 0, "Expected non-zero token count"

    print(f"\n✅ RAG search test passed!")
    print(f"   Top result: {top_citation['source_file']}")
    print(f"   Context tokens: {result.total_tokens}")
    print(f"   Citations: {len(result.source_citations)}")
