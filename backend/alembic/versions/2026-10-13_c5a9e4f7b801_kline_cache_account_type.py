"""kline_cache account_type isolation (demo vs real market data)

Revision ID: c5a9e4f7b801
Revises: f3c8b2d6a901
Create Date: 2026-10-13 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c5a9e4f7b801'
down_revision: Union[str, Sequence[str], None] = 'f3c8b2d6a901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'kline_cache',
        sa.Column('account_type', sa.String(length=8), nullable=False, server_default='demo'),
    )
    # 存量回填：带经纪商后缀（.E 等）的品种来自实盘账户行情，其余归模拟盘
    op.execute("UPDATE kline_cache SET account_type = 'real' WHERE symbol LIKE '%.%'")
    op.drop_constraint('uq_kline_cache_symbol_tf', 'kline_cache', type_='unique')
    op.create_unique_constraint(
        'uq_kline_cache_symbol_tf_type', 'kline_cache', ['symbol', 'timeframe', 'account_type']
    )
    op.create_index('ix_kline_cache_account_type', 'kline_cache', ['account_type'])


def downgrade() -> None:
    op.drop_index('ix_kline_cache_account_type', table_name='kline_cache')
    op.drop_constraint('uq_kline_cache_symbol_tf_type', 'kline_cache', type_='unique')
    op.create_unique_constraint('uq_kline_cache_symbol_tf', 'kline_cache', ['symbol', 'timeframe'])
    op.drop_column('kline_cache', 'account_type')
