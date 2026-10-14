"""
Market request/response schemas.

Field names mirror ``frontend/src/types/api.ts`` — that file is the
contract; keep the two in sync.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

MarketTimeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]

SymbolGroup = Literal["forex", "indices", "commodities", "crypto", "stocks"]


class SymbolOut(BaseModel):
    """Mirrors the frontend ``Symbol`` type."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    group: SymbolGroup
    base_currency: str
    quote_currency: str
    digits: int
    point: float
    spread: int
    tick_size: float
    tick_value: float
    contract_size: float
    min_volume: float
    max_volume: float
    volume_step: float
    swap_long: float
    swap_short: float
    margin_percent: float
    is_trade_allowed: bool
    is_active: bool
    session_open: str
    session_close: str


class QuoteOut(BaseModel):
    """Mirrors the frontend ``Quote`` type."""

    model_config = ConfigDict(extra="forbid")

    symbol_id: str
    symbol_name: str
    bid: float
    ask: float
    last: float
    change: float
    change_percent: float
    spread: int
    time: str
    # 与 K 线同轴（UTC 伪 epoch）的 tick 时间 ISO 串；缺失时为 None（前端回退 time）
    bar_time: str | None = None
    high_day: float
    low_day: float
    volume: float


class PriceBarOut(BaseModel):
    """Mirrors the frontend ``PriceBar`` type (time = unix seconds)."""

    model_config = ConfigDict(extra="forbid")

    time: int
    open: float
    high: float
    low: float
    close: float
    tick_volume: int | None = None
    real_volume: int | None = None
    spread: int | None = None


class SymbolListResponse(BaseModel):
    items: list[SymbolOut]
    total: int


class QuoteListResponse(BaseModel):
    items: list[QuoteOut]
    total: int


# ── Data quality check (数据中心 P1: 缺口/异常值) ───────────────────

QualitySeverity = Literal["info", "warning", "error"]


class QualityIssue(BaseModel):
    """One detected anomaly, capped per category by the service layer."""

    model_config = ConfigDict(extra="forbid")

    severity: QualitySeverity
    code: str  # gap | invalid_ohlc | price_spike | duplicate_time | zero_volume
    message: str
    start_time: int | None = None
    end_time: int | None = None


class DataQualityReport(BaseModel):
    """Compute-on-read bar quality analysis for one symbol/timeframe window."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    timeframe: str
    start: int
    end: int
    bars_analyzed: int
    quality_score: int  # 0–100
    verdict: Literal["ok", "warn", "poor"]
    gap_count: int
    weekend_gap_count: int
    invalid_count: int
    spike_count: int
    duplicate_count: int
    zero_volume_count: int
    issues: list[QualityIssue]
    truncated: bool = False
