"""Portfolio backtest (PRD Phase 3 P2): one strategy × N symbols.

A portfolio row is a coordinating parent over N real single-symbol
``backtest_tasks`` children (per-symbol independent deposit = total / N).
Children carry ``config_snapshot.portfolio_id`` + ``analysis_role`` (=symbol)
so aggregation can map each child back to its symbol.  Aggregation happens
on read (same compute-on-read pattern as Walk-Forward / optimization): once
every child is terminal the combined equity curve is built (timestamp union
+ forward-fill per symbol) and the portfolio metrics are cached into
``result``.

State machine: running → completed | failed (terminal states immutable,
same convention as backtest tasks / analyses).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel

PORTFOLIO_RUNNING = "running"
PORTFOLIO_COMPLETED = "completed"
PORTFOLIO_FAILED = "failed"

PORTFOLIO_MIN_SYMBOLS = 2
PORTFOLIO_MAX_SYMBOLS = 8


class PortfolioBacktest(BaseModel, kw_only=True):
    """One multi-symbol portfolio backtest coordinating N child tasks."""

    __tablename__ = "portfolio_backtests"
    __table_args__ = (
        Index("ix_portfolio_backtests_user_created", "user_id", "created_at"),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_portfolio_backtests_status",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="Owning platform user.",
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="CASCADE"),
        nullable=False,
        comment="Strategy run across all symbols (CASCADE: children die with it).",
    )
    symbols: Mapped[list] = mapped_column(
        JSONB, nullable=False,
        comment="Ordered symbol list, 2~8 unique names.",
    )
    timeframe: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="Bar period shared by every child.",
    )
    total_deposit: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False,
        comment="Sum of per-symbol deposits actually allocated.",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PORTFOLIO_RUNNING,
        comment="running | completed | failed.",
    )
    params: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default_factory=dict,
        comment="Frozen config echo + children mapping [{symbol, task_id}].",
    )
    result: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, default=None,
        comment="Aggregated portfolio output (cached on terminal).",
    )
    error: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None,
        comment="User-facing failure detail (child failure etc.).",
    )

    def __repr__(self) -> str:
        return (
            f"<PortfolioBacktest id={self.id!r} status={self.status!r} "
            f"symbols={self.symbols!r}>"
        )
