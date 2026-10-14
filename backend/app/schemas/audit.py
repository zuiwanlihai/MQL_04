"""Audit query / verify / export schemas (M7).

Read-only surface over the write-once ``audit_logs`` hash chain.  Listing and
verification are admin-gated; the export endpoint records its own audit row
(PRD M7 ④「导出行为本身也记入审计」).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ts_ms: int
    actor_type: str
    actor_id: uuid.UUID | None
    actor_label: str
    action: str
    target_type: str
    target_id: str
    params: dict
    result: str
    error_code: str | None
    risk_decision: str | None
    rule_id: str | None
    source_ip: str
    correlation_id: str
    prev_hash: str = Field(description="前一条哈希（链式取证）")
    hash: str = Field(description="本条 sha256(prev_hash + canonical payload)")
    created_at: datetime


class AuditListResponse(BaseModel):
    items: list[AuditOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class AuditVerifyOut(BaseModel):
    total: int = Field(description="链上记录总数")
    ok: bool = Field(description="链是否完好")
    broken_index: int = Field(description="首个断链位置（-1=完好）")
    checked_at: datetime
