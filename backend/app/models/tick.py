"""Tick data management (M-Tick, PRD 数据中心 P1) models.

``tick_data`` stores raw ticks pulled from the MT5 gateway keyed by
``(symbol, time_msc)`` — a composite natural key that makes the periodic
sync idempotent via ``ON CONFLICT DO NOTHING``.  It deliberately does NOT
inherit :class:`BaseModel`: tick rows are bulk-loaded (up to millions), so
the UUID PK / created_at / updated_at overhead is skipped.

``TickSyncJob`` records one user-requested sync window and its progress;
the in-process serial executor in ``services/tick_sync.py`` walks jobs
pending → running → done/failed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BaseModel


class TickData(Base, kw_only=True):
    """One tick row (bid/ask quote or last trade) for a symbol."""

    __tablename__ = "tick_data"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    time_msc: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bid: Mapped[float] = mapped_column(default=0.0)
    ask: Mapped[float] = mapped_column(default=0.0)
    last: Mapped[float] = mapped_column(default=0.0)
    volume: Mapped[int] = mapped_column(BigInteger, default=0)
    flags: Mapped[int] = mapped_column(default=0)

    def __repr__(self) -> str:  # pragma: no cover — debug helper
        return f"<TickData {self.symbol}@{self.time_msc}>"


class TickSyncJob(BaseModel, kw_only=True):
    """A user-requested tick sync window executed by the serial queue."""

    __tablename__ = "tick_sync_jobs"
    __table_args__ = (
        Index("ix_tick_sync_jobs_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    symbol: Mapped[str] = mapped_column(String(32))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(16), default="pending"
    )  # pending | running | done | failed
    tick_count: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    def __repr__(self) -> str:  # pragma: no cover — debug helper
        return f"<TickSyncJob {self.symbol} {self.status} {self.tick_count}>"
