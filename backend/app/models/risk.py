"""Risk control (M6) models: rules and trigger events.

``RiskRule`` is the account-level safety surface the PRD mandates before any
live order leaves the platform; the pre-trade evaluator
(:mod:`app.services.risk.engine`) runs in the PARENT process, before the
gateway call — a rejected order never reaches the broker.

``RiskEvent`` is the immutable trigger log: it feeds the M5 admission 记录闸
(no risk triggers during the paper window), the observation-period reset
(halt_open_today and above clears the ramp-up timer) and the auto-demotion
trail (close_all_and_stop revokes live eligibility).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel

RULE_SCOPE_GLOBAL = "global"
RULE_SCOPE_ACCOUNT = "account"
RULE_SCOPE_RUN = "run"

RULE_TYPES = frozenset({
    "daily_max_loss",        # absolute currency loss since day start
    "daily_max_loss_pct",    # loss % of day-start equity
    "max_drawdown",          # % below peak equity
    "max_lot_per_order",     # per-order volume cap
    "max_open_positions",   # concurrent position count cap
    "max_exposure",          # same-direction notional exposure cap (currency)
    "max_orders_per_minute", # order frequency cap (run-scoped)
    "consecutive_loss_cooldown",  # N consecutive losing closes → pause M minutes
    "order_rate_window",     # max N opens per rolling W-minute window (closes excluded)
})

RULE_ACTIONS = frozenset({"reject", "halt_open_today", "close_all_and_stop"})

RULE_APPLY_TO = frozenset({"ai", "manual", "both"})


class RiskRule(BaseModel, kw_only=True):
    """One active risk-control rule."""

    __tablename__ = "risk_rules"
    __table_args__ = (
        Index("ix_risk_rules_scope_account", "scope", "account_id"),
    )

    scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RULE_SCOPE_GLOBAL,
        comment="global | account | run.",
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mt5_accounts.id", ondelete="CASCADE"),
        index=True, nullable=True, default=None,
        comment="Bound account when scope=account.",
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_runs.id", ondelete="CASCADE"),
        index=True, nullable=True, default=None,
        comment="Bound live run when scope=run.",
    )
    type: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="One of RULE_TYPES (daily_max_loss, max_drawdown, ...).",
    )
    threshold: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False,
        comment="Trigger threshold (currency amount / percent / lots / count).",
    )
    cooldown_minutes: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None,
        comment="consecutive_loss_cooldown only: pause minutes after N consecutive losses.",
    )
    window_minutes: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None,
        comment="order_rate_window only: rolling window length in minutes.",
    )
    apply_to: Mapped[str] = mapped_column(
        String(8), nullable=False, default="both",
        comment="ai | manual | both — 订单来源适用范围（策略自动下单 / 手动下单）。",
    )
    action: Mapped[str] = mapped_column(
        String(32), nullable=False, default="reject",
        comment="reject | halt_open_today | close_all_and_stop.",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="Disabled rules are ignored by the evaluator.",
    )
    note: Mapped[str] = mapped_column(
        String(200), nullable=False, default="", comment="Operator note."
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, default=None,
    )

    def __repr__(self) -> str:
        return f"<RiskRule id={self.id!r} type={self.type!r} action={self.action!r}>"


class RiskEvent(BaseModel, kw_only=True):
    """Immutable record of one rule trigger (feeds admission / demotion / audit)."""

    __tablename__ = "risk_events"
    __table_args__ = (
        Index("ix_risk_events_account_time", "account_id", "created_at"),
        Index("ix_risk_events_run_time", "run_id", "created_at"),
    )

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_runs.id", ondelete="SET NULL"),
        index=True, nullable=True, default=None,
        comment="Live run whose order triggered the rule (nullable).",
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mt5_accounts.id", ondelete="SET NULL"),
        index=True, nullable=True, default=None,
        comment="Account the trigger was evaluated against.",
    )
    paper_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paper_runs.id", ondelete="SET NULL"),
        index=True, nullable=True, default=None,
        comment="Paper run for simulated-window triggers (admission 记录闸).",
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("risk_rules.id", ondelete="SET NULL"),
        nullable=True, default=None,
        comment="Rule that fired (None for platform-side safety events).",
    )
    rule_type: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="Rule type snapshot (rule may be edited/deleted later).",
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="live",
        comment="live | paper | kill_switch | reconcile.",
    )
    threshold: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0")
    )
    observed: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0"),
        comment="Observed metric value at trigger time."
    )
    action_taken: Mapped[str] = mapped_column(
        String(32), nullable=False, default="reject",
        comment="Action executed when the rule fired."
    )
    detail: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default_factory=dict,
        comment="Order params + account snapshot at trigger time.",
    )

    def __repr__(self) -> str:
        return (
            f"<RiskEvent id={self.id!r} type={self.rule_type!r} "
            f"source={self.source!r} action={self.action_taken!r}>"
        )
