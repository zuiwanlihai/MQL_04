"""add backtest tables (tasks, reports, trades, logs)

Revision ID: b7c3d9e4a152
Revises: 4f2a91c7d3b8
Create Date: 2026-10-10 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b7c3d9e4a152'
down_revision: Union[str, Sequence[str], None] = '4f2a91c7d3b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'backtest_tasks',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False, comment='Owning platform user.'),
        sa.Column('strategy_id', sa.UUID(), nullable=False, comment='Parent strategy.'),
        sa.Column('source_code', sa.Text(), nullable=False, comment='Exact source snapshot that ran (reproducibility).'),
        sa.Column('execution_mode', sa.String(length=16), nullable=False, comment='conservative | standard | optimistic (matching tier, PRD M4).'),
        sa.Column('symbol', sa.String(length=32), nullable=False, comment='Single symbol for MVP backtests.'),
        sa.Column('timeframe', sa.String(length=8), nullable=False, comment='Bar period (M1..D1).'),
        sa.Column('start_date', sa.DateTime(timezone=True), nullable=False, comment='Replay window start (UTC).'),
        sa.Column('end_date', sa.DateTime(timezone=True), nullable=False, comment='Replay window end (UTC).'),
        sa.Column('initial_deposit', sa.Numeric(precision=18, scale=4), nullable=False, comment='Starting balance.'),
        sa.Column('leverage', sa.Integer(), nullable=False, comment='Account leverage (e.g. 100 = 1:100).'),
        sa.Column('strategy_version', sa.Integer(), nullable=True, comment='Frozen version number that ran; None = working-copy snapshot.'),
        sa.Column('status', sa.String(length=16), nullable=False, comment='pending | running | cancelling | completed | failed | cancelled'),
        sa.Column('spread_mode', sa.String(length=16), nullable=False, comment='current (per-bar spread) | fixed (spread_points).'),
        sa.Column('spread_points', sa.Numeric(precision=12, scale=2), nullable=True, comment='Fixed spread in points; required when spread_mode=fixed.'),
        sa.Column('commission_per_lot', sa.Numeric(precision=12, scale=4), nullable=False, comment='Round-turn commission per 1.0 lot.'),
        sa.Column('slippage_points', sa.Numeric(precision=12, scale=2), nullable=True, comment='Override slippage in points; None = tier default (2/1/0).'),
        sa.Column('warmup_bars', sa.Integer(), nullable=False, comment='Bars before the trading window: on_bar runs, orders rejected.'),
        sa.Column('parameters', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='Strategy ctx.declare params override map.'),
        sa.Column('config_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='Frozen assumptions: price-formula text, seed, spread snapshot, symbol_info, data fingerprint, SDK/env versions, assumption_ack.'),
        sa.Column('group_id', sa.UUID(), nullable=True, comment='Sensitivity pairing: conservative + standard share one group.'),
        sa.Column('progress_percent', sa.Integer(), nullable=False, comment='0-100 replay progress.'),
        sa.Column('error_code', sa.String(length=32), nullable=True, comment='ErrorCode enum value when the task failed.'),
        sa.Column('error_message', sa.Text(), nullable=True, comment='User-facing failure detail.'),
        sa.Column('timed_out', sa.Boolean(), nullable=False, comment='True when the 10-minute task cap terminated the run.'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=True, comment='User who submitted the task.'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_backtest_tasks_group_id', 'backtest_tasks', ['group_id'], unique=False)
    op.create_index('ix_backtest_tasks_strategy_created', 'backtest_tasks', ['strategy_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_backtest_tasks_strategy_id'), 'backtest_tasks', ['strategy_id'], unique=False)
    op.create_index('ix_backtest_tasks_user_status', 'backtest_tasks', ['user_id', 'status'], unique=False)
    op.create_index(op.f('ix_backtest_tasks_user_id'), 'backtest_tasks', ['user_id'], unique=False)

    op.create_table(
        'backtest_reports',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('task_id', sa.UUID(), nullable=False, comment='Owning task (1:1).'),
        sa.Column('total_return', sa.Numeric(precision=18, scale=4), nullable=False, comment='Final equity - initial deposit.'),
        sa.Column('total_return_percent', sa.Numeric(precision=14, scale=6), nullable=False, comment='total_return / initial_deposit × 100.'),
        sa.Column('annual_return_percent', sa.Numeric(precision=14, scale=6), nullable=False, comment='Geometric annualisation over bar count.'),
        sa.Column('annual_volatility_percent', sa.Numeric(precision=14, scale=6), nullable=False, comment='Per-bar return std × sqrt(bars_per_year).'),
        sa.Column('sharpe_ratio', sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column('sortino_ratio', sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column('calmar_ratio', sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column('recovery_factor', sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column('max_drawdown_percent', sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column('max_drawdown_amount', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('win_rate', sa.Numeric(precision=8, scale=6), nullable=False, comment='Winning trades / total closed trades (0-1).'),
        sa.Column('profit_factor', sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column('expectancy', sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column('total_trades', sa.Integer(), nullable=False),
        sa.Column('winning_trades', sa.Integer(), nullable=False),
        sa.Column('losing_trades', sa.Integer(), nullable=False),
        sa.Column('avg_win', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('avg_loss', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('avg_hold_hours', sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column('consecutive_wins', sa.Integer(), nullable=False),
        sa.Column('consecutive_losses', sa.Integer(), nullable=False),
        sa.Column('equity_curve', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='Down-sampled ≤1500 points {time,equity,balance,drawdown,baseline}.'),
        sa.Column('monthly_returns', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='{"2025-03": 4.12, …} percent per UTC month.'),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False, comment='Report computation time (UTC).'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['task_id'], ['backtest_tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('task_id', name='uq_backtest_reports_task'),
    )
    op.create_index(op.f('ix_backtest_reports_task_id'), 'backtest_reports', ['task_id'], unique=False)

    op.create_table(
        'backtest_trades',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('task_id', sa.UUID(), nullable=False, comment='Owning task.'),
        sa.Column('ticket', sa.BigInteger(), nullable=False, comment='1-based fill ticket within the task.'),
        sa.Column('direction', sa.String(length=8), nullable=False, comment='buy | sell.'),
        sa.Column('volume', sa.Numeric(precision=12, scale=4), nullable=False, comment='Normalised (lot-step) volume.'),
        sa.Column('open_time', sa.DateTime(timezone=True), nullable=False, comment='Signal bar close (UTC).'),
        sa.Column('open_price', sa.Numeric(precision=18, scale=8), nullable=False, comment='Actual fill price (bid/ask side).'),
        sa.Column('base_price', sa.Numeric(precision=18, scale=8), nullable=False, comment='Pre-cost reference price (open/close bar price) — 成交构成.'),
        sa.Column('close_time', sa.DateTime(timezone=True), nullable=True, comment='None while still open (only occurs on forced-close errors).'),
        sa.Column('close_price', sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column('profit', sa.Numeric(precision=18, scale=4), nullable=False, comment='Realised P&L net of commission (swap=0 in MVP).'),
        sa.Column('profit_percent', sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column('swap', sa.Numeric(precision=18, scale=4), nullable=False, comment='Overnight interest — P1, always 0 in MVP.'),
        sa.Column('commission', sa.Numeric(precision=18, scale=4), nullable=False, comment='Round-turn commission charged for this trade.'),
        sa.Column('spread_cost', sa.Numeric(precision=18, scale=8), nullable=False, comment='Price-side spread component (absolute price units).'),
        sa.Column('slippage_cost', sa.Numeric(precision=18, scale=8), nullable=False, comment='Adverse slippage component (absolute price units).'),
        sa.Column('comment', sa.String(length=200), nullable=False, comment='Strategy order comment.'),
        sa.Column('is_forced', sa.Boolean(), nullable=False, comment='True when closed by stop-out or end-of-run forced close.'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['task_id'], ['backtest_tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('task_id', 'ticket', name='uq_backtest_trades_task_ticket'),
    )
    op.create_index('ix_backtest_trades_task_open_time', 'backtest_trades', ['task_id', 'open_time'], unique=False)
    op.create_index(op.f('ix_backtest_trades_task_id'), 'backtest_trades', ['task_id'], unique=False)

    op.create_table(
        'backtest_logs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('task_id', sa.UUID(), nullable=False, comment='Owning task.'),
        sa.Column('seq', sa.BigInteger(), nullable=False, comment='Monotonic line number within the task.'),
        sa.Column('bar_time', sa.DateTime(timezone=True), nullable=True, comment='Bar close time the line was emitted at (UTC).'),
        sa.Column('kind', sa.String(length=8), nullable=False, comment='log (策略输出) | signal (买卖标记) | system (平台事件).'),
        sa.Column('level', sa.String(length=8), nullable=False, comment='info | warn | error.'),
        sa.Column('message', sa.String(length=2000), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='Signal payload {side,price,volume,...} for kind=signal.'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['task_id'], ['backtest_tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('task_id', 'seq', name='uq_backtest_logs_task_seq'),
    )
    op.create_index('ix_backtest_logs_task_kind', 'backtest_logs', ['task_id', 'kind'], unique=False)
    op.create_index(op.f('ix_backtest_logs_task_id'), 'backtest_logs', ['task_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_backtest_logs_task_id'), table_name='backtest_logs')
    op.drop_index('ix_backtest_logs_task_kind', table_name='backtest_logs')
    op.drop_table('backtest_logs')
    op.drop_index(op.f('ix_backtest_trades_task_id'), table_name='backtest_trades')
    op.drop_index('ix_backtest_trades_task_open_time', table_name='backtest_trades')
    op.drop_table('backtest_trades')
    op.drop_index(op.f('ix_backtest_reports_task_id'), table_name='backtest_reports')
    op.drop_table('backtest_reports')
    op.drop_index(op.f('ix_backtest_tasks_user_id'), table_name='backtest_tasks')
    op.drop_index('ix_backtest_tasks_user_status', table_name='backtest_tasks')
    op.drop_index(op.f('ix_backtest_tasks_strategy_id'), table_name='backtest_tasks')
    op.drop_index('ix_backtest_tasks_strategy_created', table_name='backtest_tasks')
    op.drop_index('ix_backtest_tasks_group_id', table_name='backtest_tasks')
    op.drop_table('backtest_tasks')
