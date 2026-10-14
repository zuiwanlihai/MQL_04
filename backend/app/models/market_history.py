"""Market data range tracking (M2 历史数据管理) model.

「已缓存数据范围」= MT5 终端本地可用的 K 线范围。K 线本身不落库
（网关为唯一权威来源），这里只维护 (symbol, timeframe) 粒度的范围
元数据快照：probe 写入 first/last/bars_count，后台同步只刷新
last_bar_time / last_synced_at / last_error。全局共享表，不分用户。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class MarketDataRange(BaseModel, kw_only=True):
    """Tracked terminal-local K-line coverage for one (symbol, timeframe)."""

    __tablename__ = "market_data_ranges"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", name="uq_md_ranges_symbol_tf"),
    )

    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    first_bar_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_bar_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    bars_count: Mapped[int] = mapped_column(default=0)
    probe_truncated: Mapped[bool] = mapped_column(default=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_error: Mapped[str | None] = mapped_column(Text, default=None)

    def __repr__(self) -> str:  # pragma: no cover — debug helper
        return f"<MarketDataRange {self.symbol}@{self.timeframe} {self.bars_count}>"
