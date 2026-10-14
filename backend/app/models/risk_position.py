"""持仓登记表：终端历史缓存不可靠时的风控数据源。

背景：专用终端实例的按日期 ``history_deals_get`` 依赖终端本地历史同步，
反复 re-login 会重置同步导致区间查询常年为空——连亏冷却/窗口限频的
deals 统计因此失效。按持仓票号 ``history_deals_get(position=...)`` 服务端
直查始终可靠。

本表登记所有"见过的持仓"：开仓时 upsert；持仓从当前列表消失即视为已平，
按票号补查成交得到平仓盈亏，供风控引擎重建平仓序列。
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Float, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class RiskPositionLog(BaseModel, kw_only=True):
    """One observed position per (account_id, ticket), with close P/L once known."""

    __tablename__ = "risk_position_log"
    __table_args__ = (
        UniqueConstraint("account_id", "ticket", name="uq_risk_poslog_acct_ticket"),
        Index("ix_risk_poslog_acct_open", "account_id", "open_time"),
        Index("ix_risk_poslog_acct_closed", "account_id", "closed_at"),
    )

    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
        comment="平台账户 id（mt5_accounts.id）。",
    )
    ticket: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="MT5 持仓票号。",
    )
    symbol: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="品种。",
    )
    open_time: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="开仓时间（epoch 秒）。",
    )
    closed_at: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, default=None,
        comment="平仓成交时间（epoch 秒）；None=仍在持仓。",
    )
    closed_profit: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None, comment="平仓净盈亏（profit+swap+commission 汇总）。",
    )

    def __repr__(self) -> str:
        state = (
            "open"
            if self.closed_at is None
            else f"closed@{self.closed_at} p/l={self.closed_profit}"
        )
        return f"<RiskPositionLog acct={self.account_id!r} ticket={self.ticket} {state}>"
