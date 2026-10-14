"""add tick data tables (M-Tick 数据管理, PRD 数据中心 P1)

Revision ID: f6a9b3d47e52
Revises: e4f8a2c95d31
Create Date: 2026-10-11 23:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f6a9b3d47e52'
down_revision: Union[str, Sequence[str], None] = 'e4f8a2c95d31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tick_data',
        sa.Column('symbol', sa.String(length=32), nullable=False,
                  comment='Broker symbol name (upper).'),
        sa.Column('time_msc', sa.BigInteger(), nullable=False,
                  comment='Tick time in UTC epoch milliseconds.'),
        sa.Column('bid', sa.Float(), nullable=False, comment='Best bid at the tick.'),
        sa.Column('ask', sa.Float(), nullable=False, comment='Best ask at the tick.'),
        sa.Column('last', sa.Float(), nullable=False,
                  comment='Last trade price (0 for quote-only instruments).'),
        sa.Column('volume', sa.BigInteger(), nullable=False,
                  comment='Integer tick volume reported by the terminal.'),
        sa.Column('flags', sa.Integer(), nullable=False,
                  comment='MT5 tick flag bitmask (TICK_BID/ASK/LAST/…).'),
        sa.PrimaryKeyConstraint('symbol', 'time_msc'),
        comment='Raw ticks pulled from the MT5 gateway; sync is idempotent '
                'via the composite natural key.',
    )
    op.create_table(
        'tick_sync_jobs',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False,
                  comment='Requesting platform user.'),
        sa.Column('symbol', sa.String(length=32), nullable=False,
                  comment='Broker symbol name (upper).'),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False,
                  comment='Window start (inclusive, UTC).'),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=False,
                  comment='Window end (exclusive, UTC).'),
        sa.Column('status', sa.String(length=16), nullable=False,
                  comment='pending | running | done | failed.'),
        sa.Column('tick_count', sa.Integer(), nullable=False,
                  comment='Rows inserted (deduped) for this job so far.'),
        sa.Column('error', sa.Text(), nullable=True,
                  comment='Failure detail when status=failed.'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_tick_sync_jobs_user_created', 'tick_sync_jobs',
        ['user_id', 'created_at'], unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_tick_sync_jobs_user_created', table_name='tick_sync_jobs')
    op.drop_table('tick_sync_jobs')
    op.drop_table('tick_data')
