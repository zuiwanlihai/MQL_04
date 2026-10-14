"""Paper trading (M5) models: runs, trades, logs.

A ``PaperRun`` is a long-lived simulated account driven by *incrementally
delivered closed bars* from the gateway — the same FillModel/AccountSim
matching kernel as M4 backtests (PRD 一致性原则), but with no end-of-run
forced close: positions persist across bars until the user stops (optionally
closing) or a stop-out fires.

``config_snapshot`` freezes the assumption surface at start time (tier text,
symbol info, spread snapshot, seed); the live account state (balance/equity/
positions) is mirrored onto the run row via throttled ``account`` events so
the status page can poll it without touching the sandbox process.

State machine (CAS via conditional UPDATE):
    pending -> running -> paused -> running
    running|paused -> stopping -> stopped        (user stop, optional close)
    running|paused|stopping -> failed            (crash / restart sweep)
``stopped`` and ``failed`` are terminal; ``reset`` creates a fresh run row.
"""

from __future__ import annotations

import uuid
from datetime import datetime
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

# Run status values (closed set — mirrors schemas/paper.py Literal)
RUN_PENDING = "pending"
RUN_RUNNING = "running"
RUN_PAUSED = "paused"
RUN_STOPPING = "stopping"
RUN_STOPPED = "stopped"
RUN_FAILED = "failed"

TERMINAL_RUN_STATUSES = frozenset({RUN_STOPPED, RUN_FAILED})


class PaperRun(BaseModel, kw_only=True):
    """One long-lived paper-trading instance of a strategy version."""

    __tablename__ = "paper_runs"
    __table_args__ = (
        Index("ix_paper_runs_user_status", "user_id", "status"),
        Index("ix_paper_runs_strategy_created", "strategy_id", "created_at"),
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
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mt5_accounts.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
        default=None,
        comment="Bound MT5 account (M12 gateway routing); NULL = legacy "
        "default node (backward-compatible rows).",
    )
    source_code: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="Exact source snapshot that runs (reproducibility).",
    )
    strategy_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None,
        comment="Frozen version number; None = working-copy snapshot at start.",
    )
    symbol: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="Single symbol for MVP paper runs."
    )
    timeframe: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="Bar period (M1..D1)."
    )
    execution_mode: Mapped[str] = mapped_column(
        String(16), nullable=False,
        comment="standard | optimistic (conservative needs a next bar).",
    )
    initial_deposit: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, comment="Starting virtual balance."
    )
    leverage: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Account leverage (e.g. 100 = 1:100)."
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
        comment="Override slippage in points; None = tier default (1/0).",
    )
    warmup_bars: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100,
        comment="History bars replayed at start before orders are allowed.",
    )
    parameters: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default_factory=dict,
        comment="Strategy ctx.declare params override map.",
    )
    config_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default_factory=dict,
        comment="Frozen assumptions: tier text, seed, symbol_info, spread "
        "snapshot, SDK/env versions, assumption_ack.",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RUN_PENDING,
        comment="pending | running | paused | stopping | stopped | failed",
    )
    error_code: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None,
        comment="ErrorCode enum value when the run failed.",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, comment="User-facing failure detail."
    )
    # ── live account mirror (throttled from runner `account` events) ──
    balance: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False,
        comment="Last mirrored virtual balance.",
    )
    equity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False,
        comment="Last mirrored virtual equity (bid close).",
    )
    margin_used: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    margin_free: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    unrealized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    positions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default_factory=list,
        comment="Open-position snapshot [{id,ticket,direction,volume,open_price,"
        "current_price,unrealized_pnl,opened_at}].",
    )
    last_bar_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="Close time of the last processed bar (poll cursor, UTC).",
    )
    equity_history: Mapped[list] = mapped_column(
        JSONB, nullable=False, default_factory=list,
        comment="Append-only equity points {time,equity,balance}, ≤5000.",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, default=None,
        comment="User who started the run.",
    )

    def __repr__(self) -> str:
        return f"<PaperRun id={self.id!r} status={self.status!r} symbol={self.symbol!r}>"


class PaperTrade(BaseModel, kw_only=True):
    """One closed round trip inside a paper run."""

    __tablename__ = "paper_trades"
    __table_args__ = (
        UniqueConstraint("run_id", "ticket", name="uq_paper_trades_run_ticket"),
        Index("ix_paper_trades_run_close_time", "run_id", "close_time"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paper_runs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="Owning run.",
    )
    ticket: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="1-based fill ticket within the run."
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
        comment="Pre-cost reference price (bar close) — 成交构成."
    )
    close_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="Closing bar close (UTC)."
    )
    close_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False
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
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    spread_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=Decimal("0")
    )
    slippage_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=Decimal("0")
    )
    comment: Mapped[str] = mapped_column(
        String(200), nullable=False, default="", comment="Strategy order comment."
    )
    is_forced: Mapped[bool] = mapped_column(
        nullable=False, default=False,
        comment="True when closed by stop-out or user stop-with-close.",
    )
    suspect: Mapped[bool] = mapped_column(
        nullable=False, default=False,
        comment=(
            "成交产生于被回调超时熔断打断的那次回调内——回调已被判失败，"
            "这笔只是模拟账本上的既成事实，统计/复盘时不应与正常成交等同看待。"
        ),
    )

    def __repr__(self) -> str:
        return f"<PaperTrade run_id={self.run_id!r} ticket={self.ticket!r} profit={self.profit!r}>"


class PaperLog(BaseModel, kw_only=True):
    """Interleaved strategy log / signal marker / system event line."""

    __tablename__ = "paper_logs"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_paper_logs_run_seq"),
        Index("ix_paper_logs_run_kind", "run_id", "kind"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paper_runs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="Owning run.",
    )
    seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="Monotonic line number within the run."
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
        return f"<PaperLog run_id={self.run_id!r} seq={self.seq!r} kind={self.kind!r}>"
