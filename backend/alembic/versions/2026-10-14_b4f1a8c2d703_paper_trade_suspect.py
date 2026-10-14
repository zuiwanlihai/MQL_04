"""paper_trades suspect flag (2.3 超时后的成交泄漏)

Revision ID: b4f1a8c2d703
Revises: a7c9e1f3b205
Create Date: 2026-10-14 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b4f1a8c2d703'
down_revision: Union[str, Sequence[str], None] = 'a7c9e1f3b205'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'paper_trades',
        sa.Column('suspect', sa.Boolean(), nullable=False, server_default=sa.false(),
                  comment='成交产生于被回调超时熔断打断的那次回调内。'),
    )


def downgrade() -> None:
    op.drop_column('paper_trades', 'suspect')
