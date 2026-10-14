"""portfolio_backtests table (P2 multi-symbol portfolio backtest)

Revision ID: f2b9d4e6a820
Revises: b8f3e2a6c914
Create Date: 2026-10-12 06:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f2b9d4e6a820'
down_revision: Union[str, Sequence[str], None] = 'b8f3e2a6c914'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'portfolio_backtests',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('strategy_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('symbols', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('timeframe', sa.String(8), nullable=False),
        sa.Column('total_deposit', sa.Numeric(18, 4), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], ondelete='CASCADE'),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name='ck_portfolio_backtests_status',
        ),
    )
    op.create_index(
        'ix_portfolio_backtests_user_created',
        'portfolio_backtests',
        ['user_id', 'created_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_portfolio_backtests_user_created', table_name='portfolio_backtests')
    op.drop_table('portfolio_backtests')
