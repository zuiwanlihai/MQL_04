"""backtest_tasks.rating column (user 1-5 star rating)

Revision ID: c8e4b2d6f901
Revises: a9c1d5e7f201
Create Date: 2026-10-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c8e4b2d6f901'
down_revision: Union[str, Sequence[str], None] = 'a9c1d5e7f201'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'backtest_tasks',
        sa.Column(
            'rating',
            sa.Integer(),
            nullable=True,
            comment='User rating 1-5 for this backtest result; NULL = unrated.',
        ),
    )


def downgrade() -> None:
    op.drop_column('backtest_tasks', 'rating')
