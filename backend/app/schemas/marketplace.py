"""Strategy marketplace (PRD Phase 3) request/response schemas.

Mirrors ``frontend/src/types/api.ts``.  ``ListingOut`` never carries
``source_code`` — only ``GET /market/listings/{id}`` (detail) returns it.
``ListingCreate.disclaimer_ack`` must be true (legal boundary: 仅工具分享，
不涉及信号售卖 — PRD 合规红线).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

StrategyLanguage = Literal["mql5", "python"]
StrategyDirection = Literal["long_only", "short_only", "both"]
Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
ListingStatus = Literal["active", "removed"]

DISCLAIMER_TEXT = (
    "我确认该策略仅为工具分享，不构成投资建议，不含信号售卖/代客理财内容，"
    "且我拥有其分享权利；使用者需自行验证策略风险。"
)


class ListingCreate(BaseModel):
    strategy_id: uuid.UUID = Field(description="要发布的策略（须为本人所有）")
    disclaimer_ack: bool = Field(description=f"必须为 true：{DISCLAIMER_TEXT}")

    @model_validator(mode="after")
    def _check(self) -> "ListingCreate":
        if not self.disclaimer_ack:
            raise ValueError(f"发布前必须确认免责声明：{DISCLAIMER_TEXT}")
        return self


class ListingOut(BaseModel):
    """List/browse row WITHOUT source_code."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    strategy_id: uuid.UUID | None
    author_id: uuid.UUID
    author_username: str = ""
    strategy_name: str
    description: str
    language: StrategyLanguage
    direction: StrategyDirection
    symbol_ids: list[str]
    timeframe: str
    tags: list[str]
    import_count: int
    created_at: datetime


class ListingDetailOut(ListingOut):
    """Detail response adds the frozen source."""

    source_code: str


class ListingListResponse(BaseModel):
    items: list[ListingOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class ListingImportRequest(BaseModel):
    name: str = Field(
        min_length=1, max_length=120,
        description="导入副本的新策略名（导入者命名空间内唯一）",
    )
