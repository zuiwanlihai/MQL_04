"""
UserSession model — database-backed refresh-token sessions.

Each row stores the SHA-256 hash of an opaque refresh token.  The raw
token is only ever held by the client.  Refresh rotates the hash (and
extends the expiry); logout sets ``revoked_at`` which immediately
invalidates the session.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.user import User


class UserSession(BaseModel, kw_only=True):
    """A single login session (refresh-token record) for a user."""

    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="Owner of this session.",
    )
    refresh_token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False,
        comment="SHA-256 hex digest of the opaque refresh token.",
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(255), nullable=True, default=None,
        comment="Client User-Agent at login time.",
    )
    ip_address: Mapped[str | None] = mapped_column(
        String(45), nullable=True, default=None,
        comment="Client IP (IPv4/IPv6) at login time.",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="Session expiry (refresh token TTL).",
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="Set when the session is logged out / revoked.",
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="Last successful refresh.",
    )

    user: Mapped["User"] = relationship(
        "User", back_populates="sessions", default=None,
    )

    @property
    def is_valid(self) -> bool:
        """True while the session is neither revoked nor expired."""
        from datetime import timezone

        now = datetime.now(timezone.utc)
        return self.revoked_at is None and self.expires_at > now

    def __repr__(self) -> str:
        return f"<UserSession id={self.id!r} user_id={self.user_id!r}>"
