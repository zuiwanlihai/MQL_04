"""Notification channel schemas (M10).

``target`` is a secret (webhook URL / bot token) — list/detail responses
always return the masked form; it is only ever accepted inbound.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ChannelKind = Literal["email", "telegram", "dingtalk", "wecom"]
AlertLevel = Literal["info", "warn", "error", "critical"]


class ChannelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ChannelKind
    target: str = Field(min_length=3, max_length=500)
    min_level: AlertLevel = "warn"


class ChannelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_level: AlertLevel | None = None
    enabled: bool | None = None


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: ChannelKind
    target_masked: str
    min_level: str
    enabled: bool
    verified_at: datetime | None
    created_at: datetime


class ChannelTestOut(BaseModel):
    ok: bool
    detail: str
