"""add market_listings (策略市场, PRD Phase 3)

Revision ID: b3d8f1a5c7e2
Revises: a3c8e5d7b914
Create Date: 2026-10-12 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b3d8f1a5c7e2'
down_revision: Union[str, Sequence[str], None] = 'a3c8e5d7b914'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'market_listings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('strategy_id', sa.UUID(), nullable=True,
                  comment='Source strategy (provenance only; snapshot is independent).'),
        sa.Column('author_id', sa.UUID(), nullable=False,
                  comment='Publisher (strategy owner at publish time).'),
        sa.Column('strategy_name', sa.String(length=120), nullable=False,
                  comment='Frozen strategy name at publish time.'),
        sa.Column('description', sa.Text(), nullable=False,
                  comment='Frozen description.'),
        sa.Column('language', sa.String(length=16), nullable=False,
                  comment='python | mql5 (MVP only python).'),
        sa.Column('direction', sa.String(length=16), nullable=False,
                  comment='long_only | short_only | both'),
        sa.Column('symbol_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  comment="Frozen declared symbols, e.g. ['EURUSD']."),
        sa.Column('timeframe', sa.String(length=8), nullable=False,
                  comment='Primary bar period (M1/M5/M15/M30/H1/H4/D1).'),
        sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  comment='Frozen tags for filtering.'),
        sa.Column('source_code', sa.Text(), nullable=False,
                  comment='Frozen source code.'),
        sa.Column('source_hash', sa.String(length=16), nullable=False,
                  comment='sha256(source_code)[:16] duplicate-publish lookup.'),
        sa.Column('status', sa.String(length=16), nullable=False,
                  comment='active | removed (soft delete).'),
        sa.Column('import_count', sa.Integer(), nullable=False,
                  comment='How many times this listing was imported.'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['author_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("status IN ('active', 'removed')",
                           name='ck_market_listings_status'),
        sa.CheckConstraint("direction IN ('long_only', 'short_only', 'both')",
                           name='ck_market_listings_direction'),
    )
    op.create_index('ix_market_listings_status_created', 'market_listings',
                    ['status', 'created_at'], unique=False)
    op.create_index('ix_market_listings_author', 'market_listings', ['author_id'],
                    unique=False)
    op.create_index('ix_market_listings_source_hash', 'market_listings',
                    ['source_hash'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_market_listings_source_hash', table_name='market_listings')
    op.drop_index('ix_market_listings_author', table_name='market_listings')
    op.drop_index('ix_market_listings_status_created', table_name='market_listings')
    op.drop_table('market_listings')
