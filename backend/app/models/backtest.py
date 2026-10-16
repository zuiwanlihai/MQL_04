"""Backtest center (M4) models: tasks, reports, trades, logs.

``backtest_tasks`` is the async job row — its ``config_snapshot`` freezes
the matching-assumption surface (execution mode + price formula text, seed,
spread snapshot, symbol info, data fingerprint, SDK/dependency versions) at
submission time so every report stays reproducible.  ``source_code`` holds
the exact snapshot that ran: frozen-version code when ``strategy_version``
is set, otherwise the working copy at submission time.

``backtest_reports`` (1:1 with a completed task) carries the metric columns
plus the down-sampled equity curve; ``backtest_trades`` stores every round
trip with its execution-price breakdown (base/spread/slippage/commission);
``backtest_logs`` interleaves strategy output, signal markers and system
events (capped at 5000 rows per task).

State machine (CAS via conditional UPDATE, never read-modify-write):
    pending -> running -> completed | failed | cancelled
    running -> cancelling -> cancelled        (user cancel of a live task)
Terminal states are immutable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel

# Task status values (closed set — mirrors schemas/backtest.py Literal)
TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_CANCELLING = "cancelling"
TASK_COMPLETED = "completed"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"

TERMINAL_STATUSES = frozenset({TASK_COMPLETED, TASK_FAILED, TASK_CANCELLED})


class BacktestTask(BaseModel, kw_only=True):
    """An asynchronous backtest job with its frozen configuration snapshot."""

    __tablename__ = "backtest_tasks"
    __table_args__ = (
        Index("ix_backtest_tasks_user_status", "user_id", "status"),
        Index("ix_backtest_tasks_strategy_created", "strategy_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="Owning platform user.",
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="Parent strategy.",
    )
    source_code: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="Exact source snapshot that ran (reproducibility).",
    )
    execution_mode: Mapped[str] = mapped_column(
        String(16), nullable=False,
        comment="conservative | standard | optimistic (matching tier, PRD M4).",
    )
    symbol: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="Single symbol for MVP backtests."
    )
    timeframe: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="Bar period (M1..D1)."
    )
    start_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="Replay window start (UTC)."
    )
    end_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="Replay window end (UTC)."
    )
    initial_deposit: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, comment="Starting balance."
    )
    leverage: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Account leverage (e.g. 100 = 1:100)."
    )
    strategy_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None,
        comment="Frozen version number that ran; None = working-copy snapshot.",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TASK_PENDING,
        comment="pending | running | cancelling | completed | failed | cancelled",
    )
    spread_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="current",
        comment="current (per-bar spread) | fixed (spread_points).",
    )
    spread_points: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, default=None,
        comment="Fixed spread in points; required when spread_mode=fixed.",
    )
    commission_per_lot: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0"),
        comment="Round-turn commission per 1.0 lot.",
    )
    slippage_points: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, default=None,
        comment="Override slippage in points; None = tier default (2/1/0).",
    )
    warmup_bars: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100,
        comment="Bars before the trading window: on_bar runs, orders rejected.",
    )
    parameters: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default_factory=dict,
        comment="Strategy ctx.declare params override map.",
    )
    config_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default_factory=dict,
        comment="Frozen assumptions: price-formula text, seed, spread snapshot, "
        "symbol_info, data fingerprint, SDK/env versions, assumption_ack.",
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, default=None, index=True,
        comment="Sensitivity pairing: conservative + standard share one group.",
    )
    progress_percent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="0-100 replay progress."
    )
    error_code: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None,
        comment="ErrorCode enum value when the task failed.",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, comment="User-facing failure detail."
    )
    timed_out: Mapped[bool] = mapped_column(
        nullable=False, default=False,
        comment="True when the 10-minute task cap terminated the run.",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, default=None,
        comment="User who submitted the task.",
    )
    rating: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None,
        comment="User rating 1-5 for this backtest result; NULL = unrated.",
    )
    data_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="server", server_default="server",
        comment="Bar data source: 'server' (gateway history), 'local' (client-cached bars), 'synthetic' (saved synthetic dataset).",
    )
    synthetic_dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("synthetic_datasets.id", ondelete="SET NULL"),
        index=True, nullable=True, default=None,
        comment="data_source=synthetic 时：SyntheticDataset 主键。",
    )

    def __repr__(self) -> str:
        return f"<BacktestTask id={self.id!r} status={self.status!r} mode={self.execution_mode!r}>"


class BacktestReport(BaseModel, kw_only=True):
    """Metric summary + equity curve for a completed backtest task."""

    __tablename__ = "backtest_reports"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_backtest_reports_task"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("backtest_tasks.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="Owning task (1:1).",
    )
    total_return: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, comment="Final equity - initial deposit."
    )
    total_return_percent: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, comment="total_return / initial_deposit × 100."
    )
    annual_return_percent: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, comment="Geometric annualisation over bar count."
    )
    annual_volatility_percent: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, comment="Per-bar return std × sqrt(bars_per_year)."
    )
    sharpe_ratio: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0")
    )
    sortino_ratio: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0")
    )
    calmar_ratio: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0")
    )
    recovery_factor: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0")
    )
    max_drawdown_percent: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0")
    )
    max_drawdown_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    win_rate: Mapped[Decimal] = mapped_column(
        Numeric(8, 6), nullable=False, default=Decimal("0"),
        comment="Winning trades / total closed trades (0-1).",
    )
    profit_factor: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0")
    )
    expectancy: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False, default=Decimal("0")
    )
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    winning_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    losing_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_win: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    avg_loss: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    avg_hold_hours: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0")
    )
    consecutive_wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_swap: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0"),
        comment="Sum of per-trade overnight swap (negative = net cost).",
    )
    equity_curve: Mapped[list] = mapped_column(
        JSONB, nullable=False, default_factory=list,
        comment="Down-sampled ≤1500 points {time,equity,balance,drawdown,baseline}.",
    )
    monthly_returns: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default_factory=dict,
        comment='{"2025-03": 4.12, …} percent per UTC month.',
    )
    score: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, default=None,
        comment="策略综合评分：{score,grade(S-F),credibility,dimensions,highlights,shortcomings}；"
        "NULL = 该报告生成时评分逻辑尚未上线，需重跑回测。",
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default_factory=lambda: datetime.now(UTC),
        comment="Report computation time (UTC).",
    )

    def __repr__(self) -> str:
        return f"<BacktestReport task_id={self.task_id!r} total_return={self.total_return!r}>"


class BacktestTrade(BaseModel, kw_only=True):
    """One round-trip position with its execution-price breakdown."""

    __tablename__ = "backtest_trades"
    __table_args__ = (
        UniqueConstraint("task_id", "ticket", name="uq_backtest_trades_task_ticket"),
        Index("ix_backtest_trades_task_open_time", "task_id", "open_time"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("backtest_tasks.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="Owning task.",
    )
    ticket: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="1-based fill ticket within the task."
    )
    direction: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="buy | sell."
    )
    volume: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, comment="Normalised (lot-step) volume."
    )
    open_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="Signal bar close (UTC)."
    )
    open_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, comment="Actual fill price (bid/ask side)."
    )
    base_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False,
        comment="Pre-cost reference price (open/close bar price) — 成交构成."
    )
    close_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="None while still open (only occurs on forced-close errors).",
    )
    close_price: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), nullable=True, default=None
    )
    profit: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0"),
        comment="Realised P&L net of commission (swap charged to balance daily).",
    )
    profit_percent: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, default=Decimal("0")
    )
    swap: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0"),
        comment="Overnight interest attributed to this trade (≤0 = cost).",
    )
    commission: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0"),
        comment="Round-turn commission charged for this trade."
    )
    spread_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=Decimal("0"),
        comment="Price-side spread component (absolute price units)."
    )
    slippage_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=Decimal("0"),
        comment="Adverse slippage component (absolute price units)."
    )
    comment: Mapped[str] = mapped_column(
        String(200), nullable=False, default="", comment="Strategy order comment."
    )
    is_forced: Mapped[bool] = mapped_column(
        nullable=False, default=False,
        comment="True when closed by stop-out or end-of-run forced close.",
    )

    def __repr__(self) -> str:
        return (
            f"<BacktestTrade task_id={self.task_id!r} "
            f"ticket={self.ticket!r} profit={self.profit!r}>"
        )


class BacktestLog(BaseModel, kw_only=True):
    """Interleaved strategy log / signal marker / system event line."""

    __tablename__ = "backtest_logs"
    __table_args__ = (
        UniqueConstraint("task_id", "seq", name="uq_backtest_logs_task_seq"),
        Index("ix_backtest_logs_task_kind", "task_id", "kind"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("backtest_tasks.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="Owning task.",
    )
    seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="Monotonic line number within the task."
    )
    bar_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="Bar close time the line was emitted at (UTC)."
    )
    kind: Mapped[str] = mapped_column(
        String(8), nullable=False, default="log",
        comment="log (策略输出) | signal (买卖标记) | system (平台事件)."
    )
    level: Mapped[str] = mapped_column(
        String(8), nullable=False, default="info", comment="info | warn | error."
    )
    message: Mapped[str] = mapped_column(String(2000), nullable=False)
    payload: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, default=None,
        comment="Signal payload {side,price,volume,...} for kind=signal."
    )

    def __repr__(self) -> str:
        return f"<BacktestLog task_id={self.task_id!r} seq={self.seq!r} kind={self.kind!r}>"
