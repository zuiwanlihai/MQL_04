"""Alert-rule schemas (M15, PRD M7 告警规则 P0).

Per-user CRUD surface for the user-configurable alert rules evaluated by
:mod:`app.services.alert_rules`.  Cross-user access → 404 (ownership model
shared with paper/live/notify).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AlertRuleType = Literal[
    "drawdown_pct", "daily_loss_pct", "gateway_offline",
    "trade_filled", "custom_metric",
]
AlertRuleScope = Literal["global", "account"]
AlertRuleLevel = Literal["info", "warn", "error", "critical"]
AlertOperator = Literal["lt", "lte", "gt", "gte"]
CustomMetric = Literal["equity", "balance", "unrealized_pnl", "margin_free"]


class AlertRuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    type: AlertRuleType
    scope: AlertRuleScope = "global"
    account_id: uuid.UUID | None = None
    threshold: float = Field(default=0, ge=0)
    operator: AlertOperator = "gte"
    metric: CustomMetric | None = None
    level: AlertRuleLevel = "warn"
    cooldown_seconds: int = Field(default=300, ge=0, le=86400)
    note: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _check_type_fields(self):
        if self.scope == "account" and self.account_id is None:
            raise ValueError("account 级规则必须提供 account_id")
        if self.type == "custom_metric" and self.metric is None:
            raise ValueError("custom_metric 规则必须选择 metric")
        if self.type in ("drawdown_pct", "daily_loss_pct") and self.threshold <= 0:
            raise ValueError("百分比类规则的阈值必须大于 0")
        if self.type == "gateway_offline" and self.threshold < 5:
            raise ValueError("断线阈值不得小于 5 秒")
        return self


class AlertRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    threshold: float | None = Field(default=None, ge=0)
    operator: AlertOperator | None = None
    metric: CustomMetric | None = None
    level: AlertRuleLevel | None = None
    cooldown_seconds: int | None = Field(default=None, ge=0, le=86400)
    enabled: bool | None = None
    note: str | None = Field(default=None, max_length=200)


class AlertRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: str
    scope: str
    account_id: uuid.UUID | None
    threshold: float
    operator: str
    metric: str
    level: str
    cooldown_seconds: int
    enabled: bool
    note: str
    created_at: datetime
