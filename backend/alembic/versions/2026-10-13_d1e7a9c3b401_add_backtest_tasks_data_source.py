"""backtest_tasks.data_source column (server | local)

Revision ID: d1e7a9c3b401
Revises: c8e4b2d6f901
Create Date: 2026-10-13 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd1e7a9c3b401'
down_revision: Union[str, Sequence[str], None] = 'c8e4b2d6f901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'backtest_tasks',
        sa.Column(
            'data_source',
            sa.String(length=16),
            nullable=False,
            server_default='server',
            comment="Bar data source: 'server' (gateway history) or 'local' (client-cached bars).",
        ),
    )


def downgrade() -> None:
    op.drop_column('backtest_tasks', 'data_source')
