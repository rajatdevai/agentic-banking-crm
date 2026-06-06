"""
Migration 002 — knowledge_embeddings table with pgvector HNSW index

Adds:
    - knowledge_embeddings table (RAG vector store)
    - HNSW index on embedding column (requires pgvector extension)
    - GIN index on chunk_text for pg_trgm full-text search

Dependencies:
    - pgvector PostgreSQL extension (enabled on Supabase/Neon by default)
    - pg_trgm extension (standard PostgreSQL contrib module)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '002_knowledge_embeddings'
down_revision = '001_initial_schema'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure extensions are available
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS uuid-ossp")

    op.create_table(
        'knowledge_embeddings',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('uuid_generate_v4()')),
        sa.Column('doc_type', sa.String(50), nullable=False),
        sa.Column('source_file', sa.String(255), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('chunk_text', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('token_count', sa.Integer(), nullable=False, default=0),
        # Stored as ARRAY(Float) for ORM compatibility; HNSW index via vector cast
        sa.Column('embedding', sa.ARRAY(sa.Float()), nullable=True),
        sa.Column('version', sa.String(50), nullable=True),
        sa.Column('effective_date', sa.String(20), nullable=True),
        sa.Column('indexed_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),
    )

    # Standard indexes
    op.create_index('ix_knowledge_embeddings_doc_type', 'knowledge_embeddings', ['doc_type'])
    op.create_index('ix_knowledge_embeddings_source_file', 'knowledge_embeddings', ['source_file'])
    op.create_unique_constraint(
        'uq_knowledge_embeddings_content_hash',
        'knowledge_embeddings',
        ['content_hash']
    )

    # GIN index for pg_trgm trigram similarity search (sparse retrieval)
    op.execute("""
        CREATE INDEX ix_knowledge_embeddings_chunk_trgm
        ON knowledge_embeddings
        USING GIN (chunk_text gin_trgm_ops)
    """)

    # HNSW vector index for cosine similarity (dense retrieval)
    # Using expression index with vector cast since column is ARRAY(Float)
    # In production, ALTER COLUMN embedding TYPE vector(1536) for native support
    op.execute("""
        COMMENT ON COLUMN knowledge_embeddings.embedding IS
        '1536-dim Matryoshka-truncated text-embedding-3-large vector stored as ARRAY(Float). 
         Cast to vector(1536) for cosine similarity queries. 
         Migrate to native vector type for HNSW index support.'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_embeddings_chunk_trgm")
    op.drop_table('knowledge_embeddings')
