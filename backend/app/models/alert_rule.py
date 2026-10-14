"""User-configurable alert rules (PRD M7 告警规则 P0：回撤/断线/成交/自定义条件).

System alerts (reconcile / kill-switch / gateway crash) are hardcoded events;
this table is the *user-facing* rule surface: each row describes one metric
condition that, when breached, emits an ``AlertEvent`` (source=``alert_rule``)
and fans out through the M10 notification channels.

Evaluation happens in the parent-process background loop
(:mod:`app.services.alert_rules`) against the run/account/node mirrors —
rules never touch the sandbox or the gateway directly.

``scope`` semantics:
    global  — every active run (live + paper) owned by the user; for
              ``gateway_offline`` every registered gateway node.
    account — only runs bound to ``account_id``; for ``gateway_offline``
              the node that account is bound to.
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel

ALERT_RULE_TYPES = frozenset({
    "drawdown_pct",      # equity drop from peak (live peak_equity / paper equity_history)
    "daily_loss_pct",    # live runs: loss % of day_start_equity
    "gateway_offline",   # node non-online continuously for >= threshold seconds
    "trade_filled",      # every new live/paper trade deal
    "custom_metric",     # operator/threshold over a run mirror field
})

ALERT_RULE_SCOPES = frozenset({"global", "account"})

CUSTOM_METRICS = frozenset({
    "equity", "balance", "unrealized_pnl", "margin_free",
})

ALERT_OPERATORS = frozenset({"lt", "lte", "gt", "gte"})

ALERT_LEVELS = frozenset({"info", "warn", "error", "critical"})


class AlertRule(BaseModel, kw_only=True):
    """One user-defined alert condition evaluated by the rules loop."""

    __tablename__ = "alert_rules"
    __table_args__ = (
        Index("ix_alert_rules_user_enabled", "user_id", "enabled"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True, nullable=False,
        comment="Owning platform user (rules are per-user, like channels).",
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Display name shown in the alert title."
    )
    type: Mapped[str] = mapped_column(
        String(24), nullable=False,
        comment="One of ALERT_RULE_TYPES.",
    )
    scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default="global",
        comment="global | account.",
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mt5_accounts.id", ondelete="CASCADE"),
        index=True, nullable=True, default=None,
        comment="Bound account when scope=account.",
    )
    threshold: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0"),
        comment="Percent / seconds / absolute value depending on type.",
    )
    operator: Mapped[str] = mapped_column(
        String(4), nullable=False, default="gte",
        comment="Comparison for custom_metric: lt | lte | gt | gte.",
    )
    metric: Mapped[str] = mapped_column(
        String(32), nullable=False, default="",
        comment="Run mirror field for custom_metric (CUSTOM_METRICS).",
    )
    level: Mapped[str] = mapped_column(
        String(8), nullable=False, default="warn",
        comment="AlertEvent level when the rule fires.",
    )
    cooldown_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300,
        comment="Per-subject re-alert suppression window.",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="Disabled rules are ignored by the evaluator.",
    )
    note: Mapped[str] = mapped_column(
        String(200), nullable=False, default="", comment="Operator note."
    )

    def __repr__(self) -> str:
        return f"<AlertRule id={self.id!r} type={self.type!r} name={self.name!r}>"
