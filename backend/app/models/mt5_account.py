"""MT5Account model — broker accounts bound to the platform (M1).

The password is stored only as a Fernet-encrypted blob
(``password_encrypted``) and is never exposed by any response schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class MT5Account(BaseModel, kw_only=True):
    """A broker account bound through the gateway."""

    __tablename__ = "mt5_accounts"
    __table_args__ = (UniqueConstraint("login", "server", name="uq_mt5_accounts_login_server"),)

    login: Mapped[int] = mapped_column(
        BigInteger, index=True, nullable=False,
        comment="MT5 account number.",
    )
    server: Mapped[str] = mapped_column(
        String(128), nullable=False,
        comment="Broker server name, e.g. 'MetaQuotes-Demo'.",
    )
    name: Mapped[str] = mapped_column(
        String(128), nullable=False, default="",
        comment="Account display name reported by the broker.",
    )
    account_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="demo",
        comment="demo | real | contest",
    )
    leverage: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Account leverage, e.g. 100 for 1:100.",
    )
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, default="USD",
        comment="Account currency.",
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0,
        comment="Account balance.",
    )
    credit: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0,
        comment="Broker-provided credit.",
    )
    equity: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0,
        comment="Equity (balance + floating PnL).",
    )
    margin: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0,
        comment="Used margin.",
    )
    margin_free: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0,
        comment="Free margin.",
    )
    margin_level: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0,
        comment="Margin level percent.",
    )
    is_connected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="Whether the gateway currently sees the broker connection.",
    )
    is_trade_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="Whether trading is allowed on the account.",
    )
    live_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="M6 admission ①: per-account live-trading eligibility (灰度).",
    )
    trade_frozen: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="Frozen by reconciliation mismatch — pre-trade gate rejects all.",
    )
    algo_trading_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="允许算法交易：False 时实盘策略自动下单被前置闸拒绝（手动交易不受限）。",
    )
    gateway_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gateway_nodes.id", ondelete="CASCADE"),
        nullable=False,
        comment="Gateway node that owns the MT5 session for this account.",
    )
    last_sync_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default_factory=lambda: datetime.now(timezone.utc),
        comment="Last successful balance/equity sync.",
    )
    password_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None,
        comment="Fernet ciphertext ('v1:' prefixed). Never returned by the API.",
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True, nullable=True, default=None,
        comment="Platform user who bound this account.",
    )

    @property
    def has_credentials(self) -> bool:
        """绑定时选择了「记住密码」→ 存储加密凭据，可一键切换登录。"""
        return self.password_encrypted is not None

    def __repr__(self) -> str:
        return f"<MT5Account id={self.id!r} login={self.login!r} server={self.server!r}>"
