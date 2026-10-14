"""Market history management (M2 数据中心) request/response schemas.

Field names mirror ``frontend/src/types/api.ts`` — keep the two in sync.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.market import MarketTimeframe


class HistoryProbeRequest(BaseModel):
    """POST /market/history/probe — probe & track one (symbol, timeframe)."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=32)
    timeframe: MarketTimeframe


class HistoryBackfillRequest(BaseModel):
    """POST /market/history/backfill — pull [start, end) into the terminal."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=32)
    timeframe: MarketTimeframe
    start: int = Field(ge=0, description="起始时间（epoch 秒，含）")
    end: int = Field(ge=0, description="结束时间（epoch 秒，不含）")


class MarketDataRangeOut(BaseModel):
    """One tracked (symbol, timeframe) coverage row."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    symbol: str
    timeframe: str
    first_bar_time: datetime | None
    last_bar_time: datetime | None
    bars_count: int
    probe_truncated: bool
    last_synced_at: datetime | None
    last_error: str | None


class HistoryCoverageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MarketDataRangeOut]
    total: int


class HistoryBackfillResponse(BaseModel):
    """Aggregated result of one backfill request (window-sliced)."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    timeframe: str
    first_bar_time: datetime | None
    last_bar_time: datetime | None
    bars_count: int
    windows: int
    attempts: int
    complete: bool
    requested_start: int
    requested_end: int


class HistorySyncResponse(BaseModel):
    """One incremental sync round summary."""

    model_config = ConfigDict(extra="forbid")

    updated: int
    failed: int


class HistoryRemoveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    timeframe: str
    removed: bool
