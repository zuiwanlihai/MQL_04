"""mt5_accounts.algo_trading_enabled column (允许算法交易账户开关)

Revision ID: e9d5b0c8a3f7
Revises: e8c4a9d2f6b1
Create Date: 2026-10-12 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e9d5b0c8a3f7'
down_revision: Union[str, Sequence[str], None] = 'e8c4a9d2f6b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'mt5_accounts',
        sa.Column(
            'algo_trading_enabled',
            sa.Boolean(),
            server_default=sa.text('true'),
            nullable=False,
            comment='允许算法交易：False 时实盘策略自动下单被前置闸拒绝（手动交易不受限）。',
        ),
    )


def downgrade() -> None:
    op.drop_column('mt5_accounts', 'algo_trading_enabled')
