"""backtest_reports.total_swap + swap modelling comments (PRD 回测中心 P1 隔夜利息)

Revision ID: c4d8f2a61b93
Revises: a7c3e1f92b48
Create Date: 2026-10-12 02:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4d8f2a61b93'
down_revision: Union[str, Sequence[str], None] = 'a7c3e1f92b48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'backtest_reports',
        sa.Column(
            'total_swap',
            sa.Numeric(18, 4),
            server_default=sa.text('0'),
            nullable=False,
            comment='Sum of per-trade overnight swap (negative = net cost).',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('backtest_reports', 'total_swap')
