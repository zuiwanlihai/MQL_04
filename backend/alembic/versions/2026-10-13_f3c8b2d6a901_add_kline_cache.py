"""kline_cache table (shared global K-line cache, not per-user)

Revision ID: f3c8b2d6a901
Revises: d1e7a9c3b401
Create Date: 2026-10-13 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

# revision identifiers, used by Alembic.
revision: str = 'f3c8b2d6a901'
down_revision: Union[str, Sequence[str], None] = 'd1e7a9c3b401'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'kline_cache',
        sa.Column('id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('symbol', sa.String(length=32), nullable=False, index=True),
        sa.Column('timeframe', sa.String(length=8), nullable=False),
        sa.Column('bars', pg.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('bar_count', sa.Integer(), nullable=False),
        sa.Column('first_time', sa.BigInteger(), nullable=False),
        sa.Column('last_time', sa.BigInteger(), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.UniqueConstraint('symbol', 'timeframe', name='uq_kline_cache_symbol_tf'),
    )


def downgrade() -> None:
    op.drop_table('kline_cache')
