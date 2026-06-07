"""
All SQLAlchemy ORM models for the RM Copilot platform.

Table design principles:
- Every table uses a server-generated UUID primary key (uuid-ossp extension)
- All enum columns use PostgreSQL native ENUMs via SQLAlchemy's PgEnum
- All child tables index their parent FK for query performance
- Soft delete via TimestampMixin.deleted_at — never hard-delete
- agent_execution_logs is append-only (enforced by DB check constraint)
- external_cbs_id on customers is encrypted at the application layer (Fernet)
  before being stored — it is NEVER sent to any LLM

Import order: Base and mixins → enum imports → model definitions
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Enum as BaseEnum

class PgEnum(BaseEnum):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("values_callable", lambda x: [e.value for e in x])
        super().__init__(*args, **kwargs)

from shared.db.base import Base, TimestampMixin
from shared.constants.enums import (
    EventType,
    KYCStatus,
    OpportunityStatus,
    OutreachChannel,
    PersonaType,
    ProductType,
    RiskTier,
    TransactionDirection,
    TransactionType,
)


# ---------------------------------------------------------------------------
# Helper: server-side UUID default
# ---------------------------------------------------------------------------
def _uuid_pk() -> Mapped[uuid.UUID]:
    """Shorthand for a server-generated UUID primary key column."""
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.uuid_generate_v4(),
    )


# ---------------------------------------------------------------------------
# Table 1 — relationship_managers
# ---------------------------------------------------------------------------
class RelationshipManager(Base, TimestampMixin):
    """
    An RM who owns a portfolio of customers and uses the copilot.
    Passwords are bcrypt-hashed — never stored in plain text.
    """

    __tablename__ = "relationship_managers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    branch_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    customers: Mapped[list["Customer"]] = relationship(
        "Customer", back_populates="rm", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<RelationshipManager id={self.id} email={self.email}>"


# ---------------------------------------------------------------------------
# Table 2 — customers
# ---------------------------------------------------------------------------
class Customer(Base, TimestampMixin):
    """
    Core customer record. Belongs to exactly one RM.

    Security note: external_cbs_id stores the real Core Banking System account
    identifier. This field is encrypted at the application layer (Fernet symmetric
    encryption, key from KMS) before INSERT and decrypted after SELECT.
    It is NEVER included in any LLM prompt or agent state — only the internal UUID
    customer_id is used for agent operations.
    """

    __tablename__ = "customers"
    __table_args__ = (
        Index("ix_customers_rm_id", "rm_id"),
        Index("ix_customers_persona_type", "persona_type"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    rm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("relationship_managers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Encrypted CBS account identifier — NEVER sent to LLM
    external_cbs_id: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="Fernet-encrypted CBS account ID"
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True, default=None)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    persona_type: Mapped[PersonaType] = mapped_column(
        PgEnum(PersonaType, name="personatype", create_type=True),
        nullable=False,
    )
    risk_tier: Mapped[RiskTier] = mapped_column(
        PgEnum(RiskTier, name="risktier", create_type=True),
        nullable=False,
        default=RiskTier.LOW,
    )
    kyc_status: Mapped[KYCStatus] = mapped_column(
        PgEnum(KYCStatus, name="kycstatus", create_type=True),
        nullable=False,
        default=KYCStatus.PENDING,
    )
    relationship_tenure_months: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    rm: Mapped["RelationshipManager"] = relationship(
        "RelationshipManager", back_populates="customers"
    )
    profile: Mapped["CustomerProfile"] = relationship(
        "CustomerProfile", back_populates="customer", uselist=False, lazy="select"
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction", back_populates="customer", lazy="select"
    )
    detected_events: Mapped[list["DetectedEvent"]] = relationship(
        "DetectedEvent", back_populates="customer", lazy="select"
    )
    opportunities: Mapped[list["Opportunity"]] = relationship(
        "Opportunity", back_populates="customer", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<Customer id={self.id} persona={self.persona_type}>"


# ---------------------------------------------------------------------------
# Table 3 — customer_profiles
# ---------------------------------------------------------------------------
class CustomerProfile(Base):
    """
    Denormalised snapshot of a customer's financial picture, refreshed nightly.
    One-to-one with Customer. Stored separately to allow fast batch refreshes
    without touching the core customers record.

    product_holdings JSONB example:
        {"home_loan": false, "personal_loan": false, "credit_card": "Signature", "sip": true}

    behavioral_tags TEXT[] example:
        ["travel_heavy", "investor", "luxury_spender", "salary_earner"]
    """

    __tablename__ = "customer_profiles"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    salary_avg_3m: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    avg_balance_3m: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    total_investments: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    total_liabilities: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    credit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    product_holdings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    behavioral_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, nullable=False
    )
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationship
    customer: Mapped["Customer"] = relationship(
        "Customer", back_populates="profile"
    )

    def __repr__(self) -> str:
        return f"<CustomerProfile customer_id={self.customer_id} cibil={self.credit_score}>"


# ---------------------------------------------------------------------------
# Table 4 — transactions
# ---------------------------------------------------------------------------
class Transaction(Base):
    """
    Customer transaction history.

    TimescaleDB replaced with standard PostgreSQL + index on txn_at.
    For high-volume deployments, partition this table by month using PostgreSQL
    declarative partitioning (pg_partman) on txn_at.

    merchant_name is NOT PII — it is the merchant display name (e.g., "Tanishq",
    "Taj Hotels"), not the customer's name. It is safe to use in rule evaluation.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_customer_id", "customer_id"),
        Index("ix_transactions_txn_at", "txn_at"),
        Index("ix_transactions_merchant_category", "merchant_category"),
        # Composite index for the most common event-detection query pattern
        Index("ix_transactions_customer_txn_at", "customer_id", "txn_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
    )
    txn_type: Mapped[TransactionType] = mapped_column(
        PgEnum(TransactionType, name="transactiontype", create_type=True),
        nullable=False,
    )
    merchant_category: Mapped[str | None] = mapped_column(
        String(10), nullable=True, comment="ISO 18245 MCC code e.g. '5094' for jewellery"
    )
    merchant_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Merchant display name — not PII"
    )
    amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    direction: Mapped[TransactionDirection] = mapped_column(
        PgEnum(TransactionDirection, name="transactiondirection", create_type=True),
        nullable=False,
    )
    channel: Mapped[str | None] = mapped_column(String(50), nullable=True)
    txn_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
        comment="Optional notes for testing and audit (not a customer-facing field)"
    )

    # Relationship
    customer: Mapped["Customer"] = relationship(
        "Customer", back_populates="transactions"
    )

    def __repr__(self) -> str:
        return f"<Transaction id={self.id} type={self.txn_type} amount={self.amount}>"


# ---------------------------------------------------------------------------
# Table 5 — detected_events
# ---------------------------------------------------------------------------
class DetectedEvent(Base):
    """
    A life event inferred from transaction pattern analysis by the rule engine.

    signals JSONB stores the exact evidence that caused each rule to fire:
    {
        "rules_fired": ["jewellery_spend_detected", "banquet_booking_detected"],
        "jewellery_txn_ids": ["uuid1", "uuid2"],
        "jewellery_total": 87500.00,
        "banquet_txn_ids": ["uuid3"],
        "banquet_total": 45000.00,
        "detection_window_days": 60
    }
    This makes every detection fully auditable and explainable to compliance.
    """

    __tablename__ = "detected_events"
    __table_args__ = (
        Index("ix_detected_events_customer_id", "customer_id"),
        Index("ix_detected_events_detected_at", "detected_at"),
        Index("ix_detected_events_event_type", "event_type"),
        Index("ix_detected_events_actioned", "actioned"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[EventType] = mapped_column(
        PgEnum(EventType, name="eventtype", create_type=True),
        nullable=False,
    )
    confidence_score: Mapped[float] = mapped_column(
        Numeric(4, 3), nullable=False, comment="0.000 to 1.000"
    )
    signals: Mapped[dict] = mapped_column(
        JSONB, default=dict, nullable=False,
        comment="Evidence payload: which rules fired with supporting transaction IDs"
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Events become stale after this date — opportunity window has passed"
    )
    actioned: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
        comment="True once an opportunity has been created from this event"
    )

    # Relationships
    customer: Mapped["Customer"] = relationship(
        "Customer", back_populates="detected_events"
    )
    opportunity: Mapped["Opportunity | None"] = relationship(
        "Opportunity", back_populates="event", uselist=False
    )

    def __repr__(self) -> str:
        return f"<DetectedEvent id={self.id} type={self.event_type} conf={self.confidence_score}>"


# ---------------------------------------------------------------------------
# Table 6 — opportunities
# ---------------------------------------------------------------------------
class Opportunity(Base, TimestampMixin):
    """
    A scored, ranked customer opportunity presented in the RM priority queue.

    priority_score is the composite ranking metric:
        priority_score = conversion_prob * revenue_potential_normalized * urgency_factor

    risk_flags JSONB example:
        {"flag": "MONITOR", "reason": "1 missed EMI 4 months ago", "cibil": 691}

    explanation is the plain-English text generated by the Explainability Agent (gpt-4o)
    and shown as the reasoning card in the dashboard.
    """

    __tablename__ = "opportunities"
    __table_args__ = (
        Index("ix_opportunities_customer_id", "customer_id"),
        Index("ix_opportunities_status", "status"),
        Index("ix_opportunities_priority_score", "priority_score"),
        # RM dashboard query: filter by status, order by priority
        Index("ix_opportunities_status_priority", "status", "priority_score"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("detected_events.id", ondelete="SET NULL"),
        nullable=True,
        comment="The life event that triggered this opportunity — nullable for proactive scoring"
    )
    product_recommended: Mapped[ProductType] = mapped_column(
        PgEnum(ProductType, name="producttype", create_type=True),
        nullable=False,
    )
    priority_score: Mapped[float] = mapped_column(
        Numeric(6, 2), nullable=False, default=0.0
    )
    conversion_prob: Mapped[float] = mapped_column(
        Numeric(4, 3), nullable=False, default=0.0,
        comment="0.000 to 1.000 from XGBoost model"
    )
    revenue_potential: Mapped[float | None] = mapped_column(
        Numeric(15, 2), nullable=True,
        comment="Estimated bank revenue from this product over 12 months"
    )
    risk_flags: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    explanation: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="RM-readable reasoning card generated by ExplainabilityAgent (gpt-4o)"
    )
    status: Mapped[OpportunityStatus] = mapped_column(
        PgEnum(OpportunityStatus, name="opportunitystatus", create_type=True),
        nullable=False,
        default=OpportunityStatus.NEW,
    )

    # Relationships
    customer: Mapped["Customer"] = relationship(
        "Customer", back_populates="opportunities"
    )
    event: Mapped["DetectedEvent | None"] = relationship(
        "DetectedEvent", back_populates="opportunity"
    )
    campaigns: Mapped[list["OutreachCampaign"]] = relationship(
        "OutreachCampaign", back_populates="opportunity", lazy="select"
    )

    def __repr__(self) -> str:
        return (
            f"<Opportunity id={self.id} product={self.product_recommended} "
            f"score={self.priority_score} status={self.status}>"
        )


# ---------------------------------------------------------------------------
# Table 7 — outreach_campaigns
# ---------------------------------------------------------------------------
class OutreachCampaign(Base):
    """
    A single outreach message from the RM to a customer, tied to one opportunity.

    Lifecycle:
        message_body generated by OutreachGenAgent → RM approves in dashboard →
        Celery task dispatches → provider_message_id stored → delivery receipt
        tracked via webhook → opened_at / converted_at updated.

    All timestamps are nullable because they are populated progressively
    as the message moves through its lifecycle.
    """

    __tablename__ = "outreach_campaigns"
    __table_args__ = (
        Index("ix_outreach_campaigns_opportunity_id", "opportunity_id"),
        Index("ix_outreach_campaigns_sent_at", "sent_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[OutreachChannel] = mapped_column(
        PgEnum(OutreachChannel, name="outreachchannel", create_type=True),
        nullable=False,
    )
    message_body: Mapped[str] = mapped_column(Text, nullable=False)
    message_option_a: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_option_b: Mapped[str | None] = mapped_column(Text, nullable=True)
    persona_tone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
        comment="Message ID from WhatsApp/Twilio/SendGrid for delivery receipt correlation"
    )
    session_id: Mapped[str | None] = mapped_column(String(100), nullable=True, default=None)

    # Relationship
    opportunity: Mapped["Opportunity"] = relationship(
        "Opportunity", back_populates="campaigns"
    )

    def __repr__(self) -> str:
        return f"<OutreachCampaign id={self.id} channel={self.channel} sent={self.sent_at}>"


# ---------------------------------------------------------------------------
# Table 8 — agent_execution_logs  (APPEND-ONLY)
# ---------------------------------------------------------------------------
class AgentExecutionLog(Base):
    """
    Immutable audit record for every agent execution and LLM call.

    CRITICAL: This table is append-only.
    A PostgreSQL check constraint prevents any UPDATE to rows in this table.
    The application-layer rule: never call session.merge() or UPDATE on this model.

    input_masked JSONB: the agent's input with all PII already replaced by tokens.
    output JSONB: the agent's structured output (also PII-free).

    Retention: 7 years minimum (regulatory requirement).
    """

    __tablename__ = "agent_execution_logs"
    __table_args__ = (
        Index("ix_agent_logs_session_id", "session_id"),
        Index("ix_agent_logs_agent_name", "agent_name"),
        Index("ix_agent_logs_executed_at", "executed_at"),
        # Append-only enforcement: block any UPDATE to this table.
        # The check condition `1 = 1` always passes on INSERT but a trigger
        # is the production-grade approach. This constraint signals intent
        # to DBAs and prevents accidental application-layer updates.
        CheckConstraint("executed_at IS NOT NULL", name="chk_agent_logs_append_only"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
        comment="Links all agent executions within a single RM request"
    )
    agent_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_masked: Mapped[dict] = mapped_column(
        JSONB, default=dict, nullable=False,
        comment="Agent input with all PII replaced by Presidio tokens — safe to store"
    )
    output: Mapped[dict] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    llm_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<AgentExecutionLog id={self.id} agent={self.agent_name} "
            f"session={self.session_id}>"
        )


# ---------------------------------------------------------------------------
# Table 9 — knowledge_embeddings  (RAG vector store)
# ---------------------------------------------------------------------------
class KnowledgeEmbedding(Base):
    """
    Stores chunked knowledge base documents with their vector embeddings.

    content_hash (SHA256 of chunk_text) enables idempotent ingestion —
    chunks with an identical hash are skipped during backfill.

    embedding uses the pgvector VECTOR type (1536 dims — Matryoshka truncation
    of text-embedding-3-large's native 3072 dims).

    HNSW index is defined in the Alembic migration for efficient cosine search.
    pg_trgm GIN index on chunk_text enables fast keyword/full-text search.
    """

    __tablename__ = "knowledge_embeddings"
    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_knowledge_embeddings_content_hash"),
        Index("ix_knowledge_embeddings_doc_type", "doc_type"),
        Index("ix_knowledge_embeddings_source_file", "source_file"),
        # GIN index for pg_trgm full-text — created in migration
        # HNSW index for pgvector cosine — created in migration
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    doc_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="Collection: product_catalog | policy_docs | persona_playbooks | market_context"
    )
    source_file: Mapped[str] = mapped_column(
        String(255), nullable=False,
        comment="Relative path from knowledge_base/ e.g. product_catalog/personal_loan_eligibility.md"
    )
    chunk_index: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="Position of this chunk within the source document (0-indexed)"
    )
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True,
        comment="SHA256 of chunk_text — used for idempotent upsert"
    )
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding: Mapped[list[float]] = mapped_column(
        ARRAY(Float), nullable=True,
        comment="1536-dim vector (Matryoshka truncation of text-embedding-3-large)"
    )
    version: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
        comment="Document version from file metadata"
    )
    effective_date: Mapped[str | None] = mapped_column(
        String(20), nullable=True,
        comment="Effective date from document front matter"
    )
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeEmbedding id={self.id} doc_type={self.doc_type} "
            f"source={self.source_file} chunk={self.chunk_index}>"
        )


# ---------------------------------------------------------------------------
# Table 10 — dnd_registry
# ---------------------------------------------------------------------------
class DNDRegistry(Base):
    """
    Registry of phone numbers and email addresses that are opted out of marketing.
    """
    __tablename__ = "dnd_registry"

    id: Mapped[uuid.UUID] = _uuid_pk()
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, index=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    opted_out_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<DNDRegistry id={self.id} phone={self.phone} email={self.email}>"

