"""backtest_analyses: allow kind='optimization' (PRD Phase 2 参数优化)

Revision ID: d9b7e4c2a516
Revises: c4d8f2a61b93
Create Date: 2026-10-12 03:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd9b7e4c2a516'
down_revision: Union[str, Sequence[str], None] = 'c4d8f2a61b93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('ck_backtest_analyses_kind', 'backtest_analyses', type_='check')
    op.create_check_constraint(
        'ck_backtest_analyses_kind',
        'backtest_analyses',
        "kind IN ('walk_forward', 'monte_carlo', 'optimization')",
    )
    op.alter_column(
        'backtest_analyses',
        'kind',
        existing_type=sa.String(length=16),
        comment='walk_forward | monte_carlo | optimization.',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'backtest_analyses',
        'kind',
        existing_type=sa.String(length=16),
        comment='walk_forward | monte_carlo.',
    )
    op.drop_constraint('ck_backtest_analyses_kind', 'backtest_analyses', type_='check')
    op.create_check_constraint(
        'ck_backtest_analyses_kind',
        'backtest_analyses',
        "kind IN ('walk_forward', 'monte_carlo')",
    )
