"""add market_data_ranges (M2 历史数据管理)

Revision ID: c7f2a9e4b1d8
Revises: d9b7e4c2a516
Create Date: 2026-10-12 03:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c7f2a9e4b1d8'
down_revision: Union[str, Sequence[str], None] = 'd9b7e4c2a516'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'market_data_ranges',
        sa.Column('symbol', sa.String(length=32), nullable=False,
                  comment='Broker symbol name (upper).'),
        sa.Column('timeframe', sa.String(length=8), nullable=False,
                  comment='M1/M5/M15/M30/H1/H4/D1.'),
        sa.Column('first_bar_time', sa.DateTime(timezone=True), nullable=True,
                  comment='Earliest terminal-local bar (probe snapshot).'),
        sa.Column('last_bar_time', sa.DateTime(timezone=True), nullable=True,
                  comment='Latest known bar (refreshed by incremental sync).'),
        sa.Column('bars_count', sa.Integer(), nullable=False,
                  comment='Terminal-local bar count at last probe.'),
        sa.Column('probe_truncated', sa.Boolean(), nullable=False,
                  comment='True when probe hit HISTORY_PROBE_MAX_BARS.'),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True,
                  comment='Rotation cursor for the background sync loop.'),
        sa.Column('last_error', sa.Text(), nullable=True,
                  comment='Last gateway error for this pair (null = healthy).'),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('symbol', 'timeframe', name='uq_md_ranges_symbol_tf'),
        comment='Tracked terminal-local K-line coverage per (symbol, timeframe).',
    )


def downgrade() -> None:
    op.drop_table('market_data_ranges')
