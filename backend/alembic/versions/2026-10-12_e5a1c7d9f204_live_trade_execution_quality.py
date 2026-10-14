"""live_trades execution-quality columns (M4 slippage calibration)

Revision ID: e5a1c7d9f204
Revises: c7f2a9e4b1d8
Create Date: 2026-10-12 04:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e5a1c7d9f204'
down_revision: Union[str, Sequence[str], None] = 'c7f2a9e4b1d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('live_trades', sa.Column(
        'bid_at_request', sa.Numeric(18, 8), nullable=True,
        comment='Broker bid at order submission (gateway snapshot).'))
    op.add_column('live_trades', sa.Column(
        'ask_at_request', sa.Numeric(18, 8), nullable=True,
        comment='Broker ask at order submission (gateway snapshot).'))
    op.add_column('live_trades', sa.Column(
        'signal_price', sa.Numeric(18, 8), nullable=True,
        comment='Signal bar close (bid) — theoretical-fill base price.'))
    op.add_column('live_trades', sa.Column(
        'signal_spread_points', sa.Numeric(12, 2), nullable=True,
        comment='Signal bar spread in points (theoretical fill spread).'))
    op.add_column('live_trades', sa.Column(
        'signal_bar_time', sa.DateTime(timezone=True), nullable=True,
        comment='Signal bar close time (latency measurement base, UTC).'))


def downgrade() -> None:
    op.drop_column('live_trades', 'signal_bar_time')
    op.drop_column('live_trades', 'signal_spread_points')
    op.drop_column('live_trades', 'signal_price')
    op.drop_column('live_trades', 'ask_at_request')
    op.drop_column('live_trades', 'bid_at_request')
