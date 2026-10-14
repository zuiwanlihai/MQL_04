"""
User model and authentication-related definitions.

The :class:`User` model is the central identity anchor for the platform.
Every trading account, strategy, alert rule, and risk rule is ultimately
owned by a user.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID  # noqa: F401  (registered via Base type_annotation_map)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    # Type-checking only — runtime resolution goes through SQLAlchemy's
    # string-based relationship registry (see comment on ``sessions``).
    from app.models.user_session import UserSession


class User(BaseModel, kw_only=True):
    """Platform user account.

    Stores authentication credentials, TOTP settings, and profile
    information.  All user-owned resources (sessions, MT5 accounts,
    strategies, rules) are reachable through the relationships defined
    here.
    """

    __tablename__ = "users"

    # -- Authentication --------------------------------------------------------
    email: Mapped[str] = mapped_column(
        String(320), unique=True, index=True, nullable=False, default=None,
        comment="Unique email address used for login.",
    )
    username: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False, default=None,
        comment="Unique username used for display and login.",
    )
    password_hash: Mapped[str] = mapped_column(
        String(255), nullable=False, default=None,
        comment="Argon2id password hash.",
    )
    full_name: Mapped[str] = mapped_column(
        String(128), nullable=False, default=None,
        comment="Human-readable full name.",
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="trader", server_default=text("'trader'"),
        comment="Platform role: admin | trader | analyst | viewer.",
    )

    # -- Two-factor authentication ---------------------------------------------
    totp_secret: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, default=None,
        comment="Fernet ciphertext of the Base32 TOTP secret (M9; plaintext never stored).",
    )
    totp_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
        comment="Whether TOTP-based 2FA is active.",
    )
    recovery_codes: Mapped[list] = mapped_column(
        JSONB, nullable=False, default_factory=list,
        comment="SHA-256 digests of the issued one-time recovery codes (M9).",
    )

    # -- Status flags ----------------------------------------------------------
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"),
        comment="When false the account is locked / deactivated.",
    )
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
        comment="Grants platform-wide admin privileges.",
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="Timestamp of the most recent successful login.",
    )

    # -- Relationships ---------------------------------------------------------
    # TODO: re-add mt5_accounts / strategies / alert_rules / risk_rules
    # relationships when those models are created — importing User into
    # app.models before then would break SQLAlchemy mapper configuration.
    sessions: Mapped[list["UserSession"]] = relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan",
        default_factory=list,
    )

    def __repr__(self) -> str:
        return f"<User id={self.id!r} email={self.email!r}>"
