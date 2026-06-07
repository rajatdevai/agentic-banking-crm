"""
Migration 003 — add_dnd_registry and pii fields

Adds:
    - dnd_registry table
    - name, phone, email columns to customers table
    - session_id column to outreach_campaigns table
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '003_add_dnd_registry'
down_revision = '002_knowledge_embeddings'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create dnd_registry table
    op.create_table(
        'dnd_registry',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('uuid_generate_v4()')),
        sa.Column('phone', sa.String(20), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('opted_out_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()'), nullable=False),
    )
    op.create_index('ix_dnd_registry_phone', 'dnd_registry', ['phone'], unique=True)
    op.create_index('ix_dnd_registry_email', 'dnd_registry', ['email'], unique=True)

    # 2. Add columns to customers
    op.add_column('customers', sa.Column('name', sa.String(255), nullable=True))
    op.add_column('customers', sa.Column('phone', sa.String(20), nullable=True))
    op.add_column('customers', sa.Column('email', sa.String(255), nullable=True))

    # 3. Add session_id column to outreach_campaigns
    op.add_column('outreach_campaigns', sa.Column('session_id', sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column('outreach_campaigns', 'session_id')
    op.drop_column('customers', 'email')
    op.drop_column('customers', 'phone')
    op.drop_column('customers', 'name')
    op.drop_table('dnd_registry')
