"""Live trading (M6) models: runs, trades, logs.

A ``LiveRun`` is a long-lived real-money instance of a strategy version bound
to an MT5 account.  It reuses the paper signal loop (same engine/gate/SDK) but
the execution boundary is the MT5 gateway: ``ctx.buy/sell/close`` become
``order_request`` events the parent forwards to the broker after the pre-trade
risk gate.  The virtual AccountSim is NOT used — balance/equity/positions are a
read-only mirror of the real broker account.

State machine (CAS via conditional UPDATE) mirrors PaperRun:
    pending -> running -> paused -> running
    running|paused -> stopping -> stopped        (user stop, optional close)
    running|paused|stopping -> failed            (crash / restart sweep)
``stopped`` and ``failed`` are terminal; ``reset`` creates a fresh run row.

Admission + observation + risk fields (M6):
    admission_snapshot  frozen four-gate metrics at deploy time (audit trail)
    observation_ends_at non-null while the conservative ramp-up window is active
    no_new_orders       set by a halt_open_today risk action (close still allowed)
    reconcile_pending   an order timed out with unknown state → reconcile converges
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
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

RUN_PENDING = "pending"
RUN_RUNNING = "running"
RUN_PAUSED = "paused"
RUN_STOPPING = "stopping"
RUN_STOPPED = "stopped"
RUN_FAILED = "failed"

TERMINAL_RUN_STATUSES = frozenset({RUN_STOPPED, RUN_FAILED})


class LiveRun(BaseModel, kw_only=True):
    """One long-lived real-money instance of a strategy version."""

    __tablename__ = "live_runs"
    __table_args__ = (
        Index("ix_live_runs_user_status", "user_id", "status"),
        Index("ix_live_runs_account_status", "account_id", "status"),
        Index("ix_live_runs_strategy_created", "strategy_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True, nullable=False,
        comment="Owning platform user.",
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="CASCADE"),
        index=True, nullable=False,
        comment="Parent strategy.",
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mt5_accounts.id", ondelete="CASCADE"),
        index=True, nullable=False,
        comment="Bound MT5 account that executes this run.",
    )
    source_code: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="Exact source snapshot that runs (reproducibility).",
    )
    strategy_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None,
        comment="Frozen version number deployed (admission is version-scoped).",
    )
    symbol: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="Single symbol for MVP live runs."
    )
    timeframe: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="Bar period (M1..D1)."
    )
    execution_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="standard",
        comment="standard only for live (fills are real broker prices).",
    )
    allocated_capital: Mapped[Decimal] = mapped_column(
        Numeric(20, 4), nullable=False,
        comment="Capital cap assigned to this run (risk denominator).",
    )
    leverage: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Account leverage (mirrored from MT5)."
    )
    magic: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0,
        comment="MT5 magic id tagging this run's orders (reconciliation key).",
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
        comment="Frozen assumptions: symbol_info, spread snapshot, seed, SDK.",
    )
    admission_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default_factory=dict,
        comment="Four-gate metrics captured at deploy (audit + display).",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RUN_PENDING,
        comment="pending | running | paused | stopping | stopped | failed",
    )
    error_code: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None,
        comment="ErrorCode enum value when the run failed."
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, comment="User-facing failure detail."
    )
    # ── observation ramp (PRD 阶梯放量) ──
    observation_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="Non-null while conservative caps apply; null after ramp-up.",
    )
    capital_cap_pct: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100,
        comment="Max % of allocated_capital usable (observation forces 10).",
    )
    lot_cap: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True, default=None,
        comment="Max per-order volume (observation = symbol volume_min).",
    )
    no_new_orders: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="halt_open_today risk action set; close still allowed.",
    )
    reconcile_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="An order timed out with unknown state; reconcile must converge.",
    )
    demoted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="Auto-demoted by a close_all_and_stop risk event; needs re-admit.",
    )
    # ── live broker mirror (throttled from gateway account/positions) ──
    balance: Mapped[Decimal] = mapped_column(
        Numeric(20, 4), nullable=False, default=Decimal("0"),
        comment="Last mirrored real balance.",
    )
    equity: Mapped[Decimal] = mapped_column(
        Numeric(20, 4), nullable=False, default=Decimal("0"),
        comment="Last mirrored real equity.",
    )
    margin_used: Mapped[Decimal] = mapped_column(
        Numeric(20, 4), nullable=False, default=Decimal("0")
    )
    margin_free: Mapped[Decimal] = mapped_column(
        Numeric(20, 4), nullable=False, default=Decimal("0")
    )
    unrealized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(20, 4), nullable=False, default=Decimal("0")
    )
    peak_equity: Mapped[Decimal] = mapped_column(
        Numeric(20, 4), nullable=False, default=Decimal("0"),
        comment="Equity high-water mark since start (max_drawdown basis).",
    )
    day_start_equity: Mapped[Decimal] = mapped_column(
        Numeric(20, 4), nullable=False, default=Decimal("0"),
        comment="Equity at the start of the current broker day (daily loss basis).",
    )
    day_start_date: Mapped[str] = mapped_column(
        String(10), nullable=False, default="",
        comment="UTC date (YYYY-MM-DD) the day_start_equity was captured for.",
    )
    halt_until: Mapped[str] = mapped_column(
        String(10), nullable=False, default="",
        comment="halt_open_today marker: UTC date until which opening is blocked.",
    )
    positions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default_factory=list,
        comment="Real open-position mirror [{ticket,symbol,type,volume,...}].",
    )
    last_bar_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="Close time of the last processed bar (poll cursor, UTC).",
    )
    equity_history: Mapped[list] = mapped_column(
        JSONB, nullable=False, default_factory=list,
        comment="Append-only equity points {time,equity,balance}, <=5000.",
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
        comment="User who deployed the run.",
    )

    def __repr__(self) -> str:
        return (
            f"<LiveRun id={self.id!r} status={self.status!r} "
            f"account={self.account_id!r} symbol={self.symbol!r}>"
        )


class LiveTrade(BaseModel, kw_only=True):
    """One real executed deal round trip (from gateway history_deals)."""

    __tablename__ = "live_trades"
    __table_args__ = (
        UniqueConstraint("run_id", "deal_ticket", name="uq_live_trades_run_deal"),
        Index("ix_live_trades_run_close_time", "run_id", "close_time"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_runs.id", ondelete="CASCADE"),
        index=True, nullable=False,
        comment="Owning run.",
    )
    deal_ticket: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="MT5 deal ticket (unique per fill)."
    )
    order_ticket: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, comment="Originating MT5 order ticket."
    )
    position_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="MT5 position identifier."
    )
    direction: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="buy | sell."
    )
    entry: Mapped[str] = mapped_column(
        String(8), nullable=False, default="in",
        comment="in | out | inout (deal entry kind)."
    )
    volume: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, comment="Filled volume."
    )
    open_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="Deal time (UTC)."
    )
    price: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, comment="Real fill price."
    )
    # ── execution-quality fields (M4 slippage calibration) ──────
    bid_at_request: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), nullable=True, default=None,
        comment="Broker bid at order submission (gateway snapshot).",
    )
    ask_at_request: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), nullable=True, default=None,
        comment="Broker ask at order submission (gateway snapshot).",
    )
    signal_price: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), nullable=True, default=None,
        comment="Signal bar close (bid) — theoretical-fill base price.",
    )
    signal_spread_points: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, default=None,
        comment="Signal bar spread in points (theoretical fill spread).",
    )
    signal_bar_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="Signal bar close time (latency measurement base, UTC).",
    )
    close_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="Set on the closing deal."
    )
    profit: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0"),
        comment="Realised P&L of the deal (broker reported)."
    )
    swap: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    commission: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    retcode: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="MT5 trade retcode."
    )
    comment: Mapped[str] = mapped_column(
        String(200), nullable=False, default="", comment="Order comment (carries req_id)."
    )
    is_forced: Mapped[bool] = mapped_column(
        nullable=False, default=False,
        comment="True when closed by Kill Switch / stop-out / risk close_all."
    )

    def __repr__(self) -> str:
        return f"<LiveTrade run_id={self.run_id!r} deal={self.deal_ticket!r}>"


class LiveLog(BaseModel, kw_only=True):
    """Interleaved strategy log / signal marker / system / risk event line."""

    __tablename__ = "live_logs"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_live_logs_run_seq"),
        Index("ix_live_logs_run_kind", "run_id", "kind"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_runs.id", ondelete="CASCADE"),
        index=True, nullable=False,
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
        comment="log | signal | system | risk."
    )
    level: Mapped[str] = mapped_column(
        String(8), nullable=False, default="info", comment="info | warn | error."
    )
    message: Mapped[str] = mapped_column(String(2000), nullable=False)
    payload: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, default=None,
        comment="Signal/risk payload for kind=signal|risk."
    )

    def __repr__(self) -> str:
        return f"<LiveLog run_id={self.run_id!r} seq={self.seq!r} kind={self.kind!r}>"
