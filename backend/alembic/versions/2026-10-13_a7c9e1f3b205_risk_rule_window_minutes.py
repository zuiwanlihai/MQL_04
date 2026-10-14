"""risk_rules window_minutes for order_rate_window rule

Revision ID: a7c9e1f3b205
Revises: f2d4a6b8c903
Create Date: 2026-10-13 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a7c9e1f3b205'
down_revision: Union[str, Sequence[str], None] = 'f2d4a6b8c903'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'risk_rules',
        sa.Column('window_minutes', sa.Integer(), nullable=True,
                  comment='order_rate_window only: rolling window length in minutes.'),
    )


def downgrade() -> None:
    op.drop_column('risk_rules', 'window_minutes')
