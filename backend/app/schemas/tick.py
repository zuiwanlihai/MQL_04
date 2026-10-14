"""Tick data management (M-Tick) request/response schemas.

Field names mirror ``frontend/src/types/api.ts`` — keep the two in sync.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TickSyncRequest(BaseModel):
    """POST /ticks/sync — request one window of ticks from the gateway."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=32)
    start: int = Field(ge=0, description="起始时间（epoch 秒，含）")
    end: int = Field(ge=0, description="结束时间（epoch 秒，不含）")


class TickSyncJobOut(BaseModel):
    """One sync job as seen by its requesting user."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str
    symbol: str
    start_time: datetime
    end_time: datetime
    status: str
    tick_count: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class TickJobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TickSyncJobOut]
    total: int


class TickCoverageOut(BaseModel):
    """Aggregated stored coverage for one symbol."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    tick_count: int
    min_time_msc: int
    max_time_msc: int


class TickCoverageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TickCoverageOut]


class TickRowOut(BaseModel):
    """One stored tick row."""

    model_config = ConfigDict(extra="forbid")

    time_msc: int
    bid: float
    ask: float
    last: float
    volume: int
    flags: int


class TickListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TickRowOut]
    total: int


class TickPurgeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    deleted: int
