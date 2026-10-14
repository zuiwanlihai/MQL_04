"""KlineCache model — shared K-line history cache (global, not per-user).

全局共享K线缓存：不区分用户，任何登录账户读写的都是同一份；
但按 **账户类型（demo/real）隔离** —— 虚拟下单与实盘的行情品种不同
（如实盘经纪商品种带 .E 后缀），各自维护独立的K线缓存。
浏览器"本地K线数据"持久化到这里，回测 data_source=local 时 worker
直接从这里读，浏览器无需随请求上传K线。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class KlineCache(BaseModel, kw_only=True):
    """One cached bar series for (symbol, timeframe) per account type."""

    __tablename__ = "kline_cache"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "timeframe", "account_type", name="uq_kline_cache_symbol_tf_type"
        ),
    )

    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    account_type: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="demo",
        server_default="demo",
        index=True,
        comment="demo|real — 虚拟下单/实盘行情缓存隔离。",
    )
    bars: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Ascending bars: {time,open,high,low,close,tick_volume,spread}.",
    )
    bar_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_time: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, comment="Earliest bar epoch seconds."
    )
    last_time: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, comment="Latest bar epoch seconds."
    )
    size_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Approx JSON size for quota display.",
    )

    def __repr__(self) -> str:
        return f"<KlineCache {self.account_type}:{self.symbol}:{self.timeframe} n={self.bar_count}>"
