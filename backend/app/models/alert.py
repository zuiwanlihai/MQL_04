"""Alert events (M6 告警渠道最小实现：站内信).

Rows are the in-site notification surface (PRD M7 MVP 渠道 = 站内 + 邮件).
Email delivery is a logged no-op until SMTP is configured (P1); every alert
also lands here so the UI bell / live pages can list and acknowledge them.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AlertEvent(BaseModel, kw_only=True):
    """One platform alert (reconcile mismatch / risk vacuum / demotion / kill)."""

    __tablename__ = "alert_events"
    __table_args__ = (
        Index("ix_alert_events_read_time", "is_read", "created_at"),
        Index("ix_alert_events_account", "account_id"),
    )

    level: Mapped[str] = mapped_column(
        String(8), nullable=False, default="warn",
        comment="info | warn | error | critical.",
    )
    source: Mapped[str] = mapped_column(
        String(24), nullable=False,
        comment="reconcile | risk_vacuum | demote | re_admit | kill_switch | "
        "risk | runner | alert_rule | gateway.",
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, default=None, index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, default=None, index=True
    )
    is_read: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="站内已读标记。"
    )
    emailed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="邮件渠道发送时间（SMTP 未配置时恒为 None）。",
    )

    def __repr__(self) -> str:
        return f"<AlertEvent id={self.id!r} source={self.source!r} level={self.level!r}>"
