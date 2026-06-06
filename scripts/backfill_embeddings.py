#!/usr/bin/env python3
"""
Backfill script — ingests the entire knowledge_base from scratch.

Usage:
    python scripts/backfill_embeddings.py

Run:
    - Once at setup after cloning the repo
    - Again whenever any knowledge base document is added or updated
    - The pipeline is idempotent — re-running skips already-indexed chunks

Environment:
    Requires DATABASE_URL and OPENAI_API_KEY in .env

What this script does:
    1. Load all markdown documents from services/rag/knowledge_base/
    2. Chunk them with paragraph-boundary aware chunker (800-token target)
    3. Embed each chunk using text-embedding-3-large (1536 dims, Matryoshka)
    4. Upsert into knowledge_embeddings table (skipping existing content_hashes)
    5. Print a summary report
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

# Bootstrap: add repo root to path so imports work
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Load .env before any imports that trigger Settings validation
from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")


async def main() -> None:
    """Run the full ingestion pipeline."""
    start_time = time.monotonic()
    print("\n=== RM Copilot — Knowledge Base Backfill ===\n")

    # --- Import after env is loaded ---
    from shared.db.session import get_async_session
    from services.rag.ingestion.loader import load_knowledge_base
    from services.rag.ingestion.chunker import chunk_documents
    from services.rag.ingestion.embedder import embed_chunks
    from services.rag.ingestion.indexer import index_embeddings, create_vector_indexes

    kb_dir = REPO_ROOT / "services" / "rag" / "knowledge_base"

    # Step 1: Load documents
    print(f"📂 Loading documents from: {kb_dir}")
    documents = load_knowledge_base(kb_dir)
    print(f"   Loaded {len(documents)} documents\n")

    for doc in documents:
        print(f"   ✓ [{doc.doc_type}] {doc.source_file} ({len(doc.raw_text):,} chars)")

    # Step 2: Chunk
    print(f"\n✂️  Chunking documents (target: 800 tokens/chunk)...")
    chunks = chunk_documents(documents)
    total_tokens = sum(c.token_count for c in chunks)
    print(f"   Created {len(chunks)} chunks | {total_tokens:,} total tokens")
    print(f"   Avg chunk size: {total_tokens // max(len(chunks), 1)} tokens\n")

    # Step 3: Embed
    print(f"🔢 Generating embeddings (text-embedding-3-large, 1536 dims)...")
    print(f"   Processing {len(chunks)} chunks in batches of 50...")
    embedded = await embed_chunks(chunks)
    print(f"   ✓ {len(embedded)} embeddings generated\n")

    # Step 4: Index
    print(f"💾 Indexing into PostgreSQL (knowledge_embeddings table)...")
    async for session in get_async_session():
        # Ensure vector indexes exist
        await create_vector_indexes(session)

        # Upsert chunks
        result = await index_embeddings(session, embedded)

        print(f"\n=== Indexing Results ===")
        print(f"   Total chunks processed : {result.total_chunks}")
        print(f"   New chunks indexed      : {result.new_chunks} ✅")
        print(f"   Skipped (already exist) : {result.skipped_chunks} ⏭️")
        print(f"   Failed                  : {result.failed_chunks} {'❌' if result.failed_chunks else '✅'}")
        break

    elapsed = time.monotonic() - start_time
    print(f"\n⏱️  Total time: {elapsed:.1f}s")
    print("\n✅ Backfill complete. Knowledge base is ready for RAG queries.\n")

    # Step 5: Quick verification query
    print("🔍 Running verification query...")
    await _verify_indexing()


async def _verify_indexing() -> None:
    """Run a quick sanity check to confirm chunks are retrievable."""
    from shared.db.session import get_async_session
    from sqlalchemy import text

    async for session in get_async_session():
        result = await session.execute(text("""
            SELECT doc_type, COUNT(*) as chunk_count
            FROM knowledge_embeddings
            GROUP BY doc_type
            ORDER BY doc_type
        """))
        rows = result.fetchall()

        print("\n📊 Chunks by collection:")
        for row in rows:
            print(f"   {row.doc_type:25s} → {row.chunk_count} chunks")

        # Check personal loan doc is indexed
        pl_result = await session.execute(text("""
            SELECT COUNT(*) FROM knowledge_embeddings
            WHERE source_file LIKE '%personal_loan_eligibility%'
        """))
        pl_count = pl_result.scalar()
        print(f"\n   personal_loan_eligibility chunks: {pl_count}")
        if pl_count and pl_count > 0:
            print("   ✅ Verification passed — personal loan doc is indexed")
        else:
            print("   ❌ Verification failed — personal loan doc not found!")
        break


if __name__ == "__main__":
    asyncio.run(main())
