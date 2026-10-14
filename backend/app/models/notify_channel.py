"""Notification channel subscriptions (M10 告警通知渠道, PRD M7 渠道 P1).

Per-user rows: which out-of-band channels (email / Telegram / DingTalk /
WeCom) a user listens on and the minimum alert level that reaches them.
``target`` holds secrets (webhook URL, bot token) — responses must always
mask it; plaintext only ever flows outbound to the provider.

Delivery is fire-and-forget from ``services.alert.emit`` — a channel that
is down must never block the in-site row (kill-switch / reconcile paths
await emit()).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel

CHANNEL_KINDS = frozenset({"email", "telegram", "dingtalk", "wecom"})
LEVEL_ORDER = ("info", "warn", "error", "critical")


class NotificationChannel(BaseModel, kw_only=True):
    """One user's subscription to an out-of-band alert channel."""

    __tablename__ = "notification_channels"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "target", name="uq_notify_user_kind_target"),
        Index("ix_notify_channels_user_enabled", "user_id", "enabled"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True, nullable=False,
        comment="Owning platform user.",
    )
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False,
        comment="email | telegram | dingtalk | wecom.",
    )
    target: Mapped[str] = mapped_column(
        String(500), nullable=False,
        comment="Email address / 'bot_token:chat_id' / webhook URL. Secrets — mask in responses.",
    )
    min_level: Mapped[str] = mapped_column(
        String(8), nullable=False, default="warn",
        comment="Only alerts at or above this level are dispatched.",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="Disabled channels are skipped by the dispatcher.",
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="Last successful test-send (email channels flip this).",
    )

    def __repr__(self) -> str:
        return f"<NotificationChannel id={self.id!r} kind={self.kind!r} user={self.user_id!r}>"
