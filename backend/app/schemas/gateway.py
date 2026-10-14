"""
Gateway node / health-log response schemas.

Mirrors ``frontend/src/types/api.ts``.  The ORM column ``metadata`` is
exposed here under its natural response name.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

GatewayNodeStatus = Literal["online", "offline", "degraded", "starting"]
GatewayNodeType = Literal["python", "ea_bridge"]


class GatewayNodeOut(BaseModel):
    """Mirrors the frontend ``GatewayNode`` type."""

    id: uuid.UUID
    name: str
    type: GatewayNodeType = "python"
    host: str
    port: int
    status: GatewayNodeStatus
    version: str
    uptime_seconds: float
    active_sessions: int
    cpu_percent: float
    memory_percent: float
    last_heartbeat: datetime | None
    is_primary: bool
    metadata: dict[str, str]
    created_at: datetime


class GatewayNodeCreate(BaseModel):
    """Body for POST /gateway/nodes (manual node registration)."""

    name: str = Field(default="本地网关", min_length=1, max_length=64)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)


class PowerFixRequest(BaseModel):
    """Body for POST /gateway/power/fix (M11)."""

    model_config = ConfigDict(extra="forbid")

    execute: bool = Field(
        default=False,
        description="false=仅返回修复命令指引；true=经网关真实修改电源方案",
    )


class GatewayNodeListResponse(BaseModel):
    items: list[GatewayNodeOut]
    total: int


class GatewayHealthLogOut(BaseModel):
    """Mirrors the frontend ``GatewayHealthLog`` type."""

    id: uuid.UUID
    node_id: uuid.UUID
    status: GatewayNodeStatus
    latency_ms: float
    error_rate: float
    message: str | None
    recorded_at: datetime


class HealthLogListResponse(BaseModel):
    items: list[GatewayHealthLogOut]
    total: int


class PairCodeOut(BaseModel):
    """One-shot gateway pairing code + QR (M17)."""

    code: str = Field(description="8-char pairing code, display form XXXX-XXXX.")
    expires_at: datetime
    server_url: str = Field(description="Backend URL the gateway should call.")
    qr_svg: str = Field(description="Inline SVG (data-uri ready) QR of the pair URI.")


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=6, max_length=12)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    name: str = Field(default="MT5 网关", max_length=64)


class PairOut(BaseModel):
    node_id: uuid.UUID
    name: str
    host: str
    port: int
    api_key: str = Field(description="Shared secret for the X-Gateway-Key header.")
    backend_version: str
