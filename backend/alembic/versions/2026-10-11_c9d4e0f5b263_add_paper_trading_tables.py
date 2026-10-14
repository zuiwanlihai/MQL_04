"""add paper trading tables (runs, trades, logs)

Revision ID: c9d4e0f5b263
Revises: b7c3d9e4a152
Create Date: 2026-10-11 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c9d4e0f5b263'
down_revision: Union[str, Sequence[str], None] = 'b7c3d9e4a152'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'paper_runs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False, comment='Owning platform user.'),
        sa.Column('strategy_id', sa.UUID(), nullable=False, comment='Parent strategy.'),
        sa.Column('source_code', sa.Text(), nullable=False, comment='Exact source snapshot that runs (reproducibility).'),
        sa.Column('strategy_version', sa.Integer(), nullable=True, comment='Frozen version number; None = working-copy snapshot at start.'),
        sa.Column('symbol', sa.String(length=32), nullable=False, comment='Single symbol for MVP paper runs.'),
        sa.Column('timeframe', sa.String(length=8), nullable=False, comment='Bar period (M1..D1).'),
        sa.Column('execution_mode', sa.String(length=16), nullable=False, comment='standard | optimistic (conservative needs a next bar).'),
        sa.Column('initial_deposit', sa.Numeric(precision=18, scale=4), nullable=False, comment='Starting virtual balance.'),
        sa.Column('leverage', sa.Integer(), nullable=False, comment='Account leverage (e.g. 100 = 1:100).'),
        sa.Column('spread_mode', sa.String(length=16), nullable=False, comment='current (per-bar spread) | fixed (spread_points).'),
        sa.Column('spread_points', sa.Numeric(precision=12, scale=2), nullable=True, comment='Fixed spread in points; required when spread_mode=fixed.'),
        sa.Column('commission_per_lot', sa.Numeric(precision=12, scale=4), nullable=False, comment='Round-turn commission per 1.0 lot.'),
        sa.Column('slippage_points', sa.Numeric(precision=12, scale=2), nullable=True, comment='Override slippage in points; None = tier default (1/0).'),
        sa.Column('warmup_bars', sa.Integer(), nullable=False, comment='History bars replayed at start before orders are allowed.'),
        sa.Column('parameters', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='Strategy ctx.declare params override map.'),
        sa.Column('config_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='Frozen assumptions: tier text, seed, symbol_info, spread snapshot, SDK/env versions, assumption_ack.'),
        sa.Column('status', sa.String(length=16), nullable=False, comment='pending | running | paused | stopping | stopped | failed'),
        sa.Column('error_code', sa.String(length=32), nullable=True, comment='ErrorCode enum value when the run failed.'),
        sa.Column('error_message', sa.Text(), nullable=True, comment='User-facing failure detail.'),
        sa.Column('balance', sa.Numeric(precision=18, scale=4), nullable=False, comment='Last mirrored virtual balance.'),
        sa.Column('equity', sa.Numeric(precision=18, scale=4), nullable=False, comment='Last mirrored virtual equity (bid close).'),
        sa.Column('margin_used', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('margin_free', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('unrealized_pnl', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('positions', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='Open-position snapshot [{id,ticket,direction,volume,open_price,current_price,unrealized_pnl,opened_at}].'),
        sa.Column('last_bar_time', sa.DateTime(timezone=True), nullable=True, comment='Close time of the last processed bar (poll cursor, UTC).'),
        sa.Column('equity_history', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='Append-only equity points {time,equity,balance}, ≤5000.'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('stopped_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=True, comment='User who started the run.'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_paper_runs_strategy_created', 'paper_runs', ['strategy_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_paper_runs_strategy_id'), 'paper_runs', ['strategy_id'], unique=False)
    op.create_index('ix_paper_runs_user_status', 'paper_runs', ['user_id', 'status'], unique=False)
    op.create_index(op.f('ix_paper_runs_user_id'), 'paper_runs', ['user_id'], unique=False)

    op.create_table(
        'paper_trades',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('run_id', sa.UUID(), nullable=False, comment='Owning run.'),
        sa.Column('ticket', sa.BigInteger(), nullable=False, comment='1-based fill ticket within the run.'),
        sa.Column('direction', sa.String(length=8), nullable=False, comment='buy | sell.'),
        sa.Column('volume', sa.Numeric(precision=12, scale=4), nullable=False, comment='Normalised (lot-step) volume.'),
        sa.Column('open_time', sa.DateTime(timezone=True), nullable=False, comment='Signal bar close (UTC).'),
        sa.Column('open_price', sa.Numeric(precision=18, scale=8), nullable=False, comment='Actual fill price (bid/ask side).'),
        sa.Column('base_price', sa.Numeric(precision=18, scale=8), nullable=False, comment='Pre-cost reference price (bar close) — 成交构成.'),
        sa.Column('close_time', sa.DateTime(timezone=True), nullable=False, comment='Closing bar close (UTC).'),
        sa.Column('close_price', sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column('profit', sa.Numeric(precision=18, scale=4), nullable=False, comment='Realised P&L net of commission (swap=0 in MVP).'),
        sa.Column('profit_percent', sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column('swap', sa.Numeric(precision=18, scale=4), nullable=False, comment='Overnight interest — P1, always 0 in MVP.'),
        sa.Column('commission', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('spread_cost', sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column('slippage_cost', sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column('comment', sa.String(length=200), nullable=False, comment='Strategy order comment.'),
        sa.Column('is_forced', sa.Boolean(), nullable=False, comment='True when closed by stop-out or user stop-with-close.'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['run_id'], ['paper_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'ticket', name='uq_paper_trades_run_ticket'),
    )
    op.create_index('ix_paper_trades_run_close_time', 'paper_trades', ['run_id', 'close_time'], unique=False)
    op.create_index(op.f('ix_paper_trades_run_id'), 'paper_trades', ['run_id'], unique=False)

    op.create_table(
        'paper_logs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('run_id', sa.UUID(), nullable=False, comment='Owning run.'),
        sa.Column('seq', sa.BigInteger(), nullable=False, comment='Monotonic line number within the run.'),
        sa.Column('bar_time', sa.DateTime(timezone=True), nullable=True, comment='Bar close time the line was emitted at (UTC).'),
        sa.Column('kind', sa.String(length=8), nullable=False, comment='log (策略输出) | signal (买卖标记) | system (平台事件).'),
        sa.Column('level', sa.String(length=8), nullable=False, comment='info | warn | error.'),
        sa.Column('message', sa.String(length=2000), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='Signal payload {side,price,volume,...} for kind=signal.'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['run_id'], ['paper_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'seq', name='uq_paper_logs_run_seq'),
    )
    op.create_index('ix_paper_logs_run_kind', 'paper_logs', ['run_id', 'kind'], unique=False)
    op.create_index(op.f('ix_paper_logs_run_id'), 'paper_logs', ['run_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_paper_logs_run_id'), table_name='paper_logs')
    op.drop_index('ix_paper_logs_run_kind', table_name='paper_logs')
    op.drop_table('paper_logs')
    op.drop_index(op.f('ix_paper_trades_run_id'), table_name='paper_trades')
    op.drop_index('ix_paper_trades_run_close_time', table_name='paper_trades')
    op.drop_table('paper_trades')
    op.drop_index(op.f('ix_paper_runs_user_id'), table_name='paper_runs')
    op.drop_index('ix_paper_runs_user_status', table_name='paper_runs')
    op.drop_index(op.f('ix_paper_runs_strategy_id'), table_name='paper_runs')
    op.drop_index('ix_paper_runs_strategy_created', table_name='paper_runs')
    op.drop_table('paper_runs')
