"""initial_schema

Creates all RM Copilot tables, PostgreSQL extensions, native enum types, and indexes.

Extension creation order matters:
  1. uuid-ossp  — provides uuid_generate_v4() used as PK server default
  2. vector     — pgvector for embedding storage (knowledge_embeddings table added in Phase 6)
  3. pg_trgm    — trigram indexes for fast text search on merchant_name etc.

All tables use UUID PKs generated server-side via uuid_generate_v4().
All enum columns use PostgreSQL native CREATE TYPE for type-safe storage.

Revision ID: 001
Revises:
Create Date: 2025-06-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# ---------------------------------------------------------------------------
# Revision identifiers
# ---------------------------------------------------------------------------
revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Step 0 — PostgreSQL extensions
    # Must run before table creation because uuid_generate_v4() is used
    # as the server_default for every primary key column.
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Check if pgvector is available
    connection = op.get_bind()
    result = connection.execute(sa.text("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'"))
    has_vector = result.scalar() > 0

    # Force disable vector extension on local dev ports 5434/5435
    if "5434" in str(connection.engine.url) or "5435" in str(connection.engine.url):
        has_vector = False

    if has_vector:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")



    # ------------------------------------------------------------------
    # Step 1 — PostgreSQL native ENUM types
    # Created explicitly so Alembic autogenerate doesn't drop/recreate
    # them on future runs when only adding columns.
    # ------------------------------------------------------------------
    personatype = postgresql.ENUM(
        "corporate_professional", "young_it_professional", "startup_founder",
        "doctor", "lawyer", "hni", "affluent_investor", "business_owner",
        "nri_family", "newly_married", "pre_retirement",
        name="personatype",
    )
    personatype.create(op.get_bind(), checkfirst=True)

    eventtype = postgresql.ENUM(
        "wedding", "home_purchase", "foreign_education", "child_education",
        "medical", "business_expansion", "promotion", "wealth_migration",
        "retirement_planning",
        name="eventtype",
    )
    eventtype.create(op.get_bind(), checkfirst=True)

    producttype = postgresql.ENUM(
        "personal_loan", "home_loan", "education_loan", "working_capital_loan",
        "loan_against_securities", "wealth_advisory", "forex_card",
        "premium_credit_card", "insurance", "business_credit_card",
        name="producttype",
    )
    producttype.create(op.get_bind(), checkfirst=True)

    risktier = postgresql.ENUM("low", "medium", "high", name="risktier")
    risktier.create(op.get_bind(), checkfirst=True)

    opportunitystatus = postgresql.ENUM(
        "new", "rm_viewed", "outreach_sent", "converted", "dismissed",
        name="opportunitystatus",
    )
    opportunitystatus.create(op.get_bind(), checkfirst=True)

    outreachchannel = postgresql.ENUM(
        "whatsapp", "sms", "email",
        name="outreachchannel",
    )
    outreachchannel.create(op.get_bind(), checkfirst=True)

    transactiontype = postgresql.ENUM(
        "upi", "card", "neft", "imps", "atm",
        name="transactiontype",
    )
    transactiontype.create(op.get_bind(), checkfirst=True)

    transactiondirection = postgresql.ENUM(
        "debit", "credit",
        name="transactiondirection",
    )
    transactiondirection.create(op.get_bind(), checkfirst=True)

    kycstatus = postgresql.ENUM(
        "complete", "pending", "expired",
        name="kycstatus",
    )
    kycstatus.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------
    # Step 2 — relationship_managers
    # ------------------------------------------------------------------
    op.create_table(
        "relationship_managers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("branch_code", sa.String(20), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_rm_email"),
    )
    op.create_index("ix_rm_email", "relationship_managers", ["email"], unique=True)

    # ------------------------------------------------------------------
    # Step 3 — customers
    # ------------------------------------------------------------------
    op.create_table(
        "customers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column(
            "rm_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("relationship_managers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("external_cbs_id", sa.String(512), nullable=True),
        sa.Column("persona_type", postgresql.ENUM("corporate_professional", "young_it_professional",
            "startup_founder", "doctor", "lawyer", "hni", "affluent_investor",
            "business_owner", "nri_family", "newly_married", "pre_retirement",
            name="personatype", create_type=False), nullable=False),
        sa.Column("risk_tier", postgresql.ENUM("low", "medium", "high",
            name="risktier", create_type=False), nullable=False, server_default="low"),
        sa.Column("kyc_status", postgresql.ENUM("complete", "pending", "expired",
            name="kycstatus", create_type=False), nullable=False, server_default="pending"),
        sa.Column("relationship_tenure_months", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_customers_rm_id", "customers", ["rm_id"])
    op.create_index("ix_customers_persona_type", "customers", ["persona_type"])

    # ------------------------------------------------------------------
    # Step 4 — customer_profiles (one-to-one with customers)
    # ------------------------------------------------------------------
    op.create_table(
        "customer_profiles",
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("salary_avg_3m", sa.Numeric(15, 2), nullable=True),
        sa.Column("avg_balance_3m", sa.Numeric(15, 2), nullable=True),
        sa.Column("total_investments", sa.Numeric(15, 2), nullable=True),
        sa.Column("total_liabilities", sa.Numeric(15, 2), nullable=True),
        sa.Column("credit_score", sa.Integer(), nullable=True),
        sa.Column("product_holdings", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'{}'")),
        sa.Column("behavioral_tags", postgresql.ARRAY(sa.String()), nullable=False,
            server_default=sa.text("'{}'")),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ------------------------------------------------------------------
    # Step 5 — transactions
    # ------------------------------------------------------------------
    op.create_table(
        "transactions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("txn_type", postgresql.ENUM("upi", "card", "neft", "imps", "atm",
            name="transactiontype", create_type=False), nullable=False),
        sa.Column("merchant_category", sa.String(10), nullable=True),
        sa.Column("merchant_name", sa.String(255), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("direction", postgresql.ENUM("debit", "credit",
            name="transactiondirection", create_type=False), nullable=False),
        sa.Column("channel", sa.String(50), nullable=True),
        sa.Column("txn_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_transactions_customer_id", "transactions", ["customer_id"])
    op.create_index("ix_transactions_txn_at", "transactions", ["txn_at"])
    op.create_index("ix_transactions_merchant_category", "transactions", ["merchant_category"])
    op.create_index("ix_transactions_customer_txn_at", "transactions",
        ["customer_id", "txn_at"])

    # ------------------------------------------------------------------
    # Step 6 — detected_events
    # ------------------------------------------------------------------
    op.create_table(
        "detected_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", postgresql.ENUM("wedding", "home_purchase", "foreign_education",
            "child_education", "medical", "business_expansion", "promotion",
            "wealth_migration", "retirement_planning",
            name="eventtype", create_type=False), nullable=False),
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=False),
        sa.Column("signals", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("detected_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actioned", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_detected_events_customer_id", "detected_events", ["customer_id"])
    op.create_index("ix_detected_events_detected_at", "detected_events", ["detected_at"])
    op.create_index("ix_detected_events_event_type", "detected_events", ["event_type"])
    op.create_index("ix_detected_events_actioned", "detected_events", ["actioned"])

    # ------------------------------------------------------------------
    # Step 7 — opportunities
    # ------------------------------------------------------------------
    op.create_table(
        "opportunities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("detected_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("product_recommended", postgresql.ENUM(
            "personal_loan", "home_loan", "education_loan", "working_capital_loan",
            "loan_against_securities", "wealth_advisory", "forex_card",
            "premium_credit_card", "insurance", "business_credit_card",
            name="producttype", create_type=False), nullable=False),
        sa.Column("priority_score", sa.Numeric(6, 2), nullable=False,
            server_default="0.00"),
        sa.Column("conversion_prob", sa.Numeric(4, 3), nullable=False,
            server_default="0.000"),
        sa.Column("revenue_potential", sa.Numeric(15, 2), nullable=True),
        sa.Column("risk_flags", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("status", postgresql.ENUM("new", "rm_viewed", "outreach_sent",
            "converted", "dismissed", name="opportunitystatus", create_type=False),
            nullable=False, server_default="new"),
        sa.Column("created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_opportunities_customer_id", "opportunities", ["customer_id"])
    op.create_index("ix_opportunities_status", "opportunities", ["status"])
    op.create_index("ix_opportunities_priority_score", "opportunities", ["priority_score"])
    op.create_index("ix_opportunities_status_priority", "opportunities",
        ["status", "priority_score"])

    # ------------------------------------------------------------------
    # Step 8 — outreach_campaigns
    # ------------------------------------------------------------------
    op.create_table(
        "outreach_campaigns",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column(
            "opportunity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("opportunities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", postgresql.ENUM("whatsapp", "sms", "email",
            name="outreachchannel", create_type=False), nullable=False),
        sa.Column("message_body", sa.Text(), nullable=False),
        sa.Column("persona_tone", sa.String(50), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_outreach_campaigns_opportunity_id", "outreach_campaigns",
        ["opportunity_id"])
    op.create_index("ix_outreach_campaigns_sent_at", "outreach_campaigns", ["sent_at"])

    # ------------------------------------------------------------------
    # Step 9 — agent_execution_logs (append-only audit table)
    # ------------------------------------------------------------------
    op.create_table(
        "agent_execution_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.String(100), nullable=True),
        sa.Column("input_masked", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'{}'")),
        sa.Column("output", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("llm_provider", sa.String(50), nullable=True),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Signals append-only intent to DBAs. Production enforcement uses a
        # PostgreSQL rule or trigger (ALTER TABLE ... ENABLE ROW LEVEL SECURITY).
        sa.CheckConstraint("executed_at IS NOT NULL", name="chk_agent_logs_append_only"),
    )
    op.create_index("ix_agent_logs_session_id", "agent_execution_logs", ["session_id"])
    op.create_index("ix_agent_logs_agent_name", "agent_execution_logs", ["agent_name"])
    op.create_index("ix_agent_logs_executed_at", "agent_execution_logs", ["executed_at"])

    # ------------------------------------------------------------------
    # Step 10 — knowledge_embeddings (pgvector — used by RAG in Phase 6)
    # Created now so the vector extension is exercised and the table is
    # present for backfill_embeddings.py in Phase 6.
    # ------------------------------------------------------------------
    connection = op.get_bind()
    result = connection.execute(sa.text("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'"))
    has_vector = result.scalar() > 0
    
    # Force disable vector extension on local dev ports 5434/5435
    if "5434" in str(connection.engine.url) or "5435" in str(connection.engine.url):
        has_vector = False
        
    embedding_type = "vector(1536)" if has_vector else "double precision[]"


    op.execute(f"""
        CREATE TABLE IF NOT EXISTS knowledge_embeddings (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            doc_type VARCHAR(50) NOT NULL,
            chunk_text TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{{}}',
            content_hash VARCHAR(64) NOT NULL,
            embedding {embedding_type},
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_knowledge_embeddings_hash UNIQUE (content_hash)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_embeddings_doc_type "
        "ON knowledge_embeddings (doc_type)"
    )
    # HNSW index for fast approximate nearest-neighbour search (only if pgvector is available)
    if has_vector:
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_embeddings_hnsw "
            "ON knowledge_embeddings USING hnsw (embedding vector_cosine_ops)"
        )



def downgrade() -> None:
    # Drop in reverse dependency order
    op.execute("DROP TABLE IF EXISTS knowledge_embeddings CASCADE")
    op.drop_table("agent_execution_logs")
    op.drop_table("outreach_campaigns")
    op.drop_table("opportunities")
    op.drop_table("detected_events")
    op.drop_table("transactions")
    op.drop_table("customer_profiles")
    op.drop_table("customers")
    op.drop_table("relationship_managers")

    # Drop PostgreSQL native enum types
    for enum_name in [
        "kycstatus", "transactiondirection", "transactiontype",
        "outreachchannel", "opportunitystatus", "risktier",
        "producttype", "eventtype", "personatype",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name} CASCADE")
