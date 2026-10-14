"""Paper daily attest (M6 admission ③).

One row per (strategy_version, account, UTC date) written by a nightly job
that aggregates PaperRun/PaperLog/RiskEvent activity for that day.  The
admission 时长闸 requires N *consecutive* recent days each with sufficient
uptime and zero violations — this table is the durable, reset-proof basis
(reset deletes runs but never these attest rows; a version change simply
produces no rows for the new version, naturally clearing the timer).
"""

from __future__ import annotations

import uuid
from datetime import date as date_type

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class PaperDailyStats(BaseModel, kw_only=True):
    """Aggregated one-day paper-trading health for a strategy version + account."""

    __tablename__ = "paper_daily_stats"
    __table_args__ = (
        UniqueConstraint(
            "strategy_id", "strategy_version", "account_id", "day",
            name="uq_paper_daily_stats_version_account_day",
        ),
    )

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    strategy_version: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Frozen version the paper ran (0=working copy)."
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mt5_accounts.id", ondelete="CASCADE"),
        nullable=True, default=None,
        comment="Target MT5 account; None = account-agnostic (MVP paper has no acct).",
    )
    day: Mapped[date_type] = mapped_column(
        Date, nullable=False, comment="UTC calendar day of the aggregate."
    )
    uptime_seconds: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0,
        comment="Seconds the paper run was live during the day (bars×tf proxy)."
    )
    bars_processed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="Closed bars fed."
    )
    risk_triggers: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="RiskEvent count (source=paper)."
    )
    sandbox_violations: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Failures with SANDBOX_*/ILLEGAL_IMPORT error codes."
    )

    def __repr__(self) -> str:
        return (
            f"<PaperDailyStats v={self.strategy_version} "
            f"day={self.day} uptime={self.uptime_seconds}>"
        )
