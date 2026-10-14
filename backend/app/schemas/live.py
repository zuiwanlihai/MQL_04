"""Live trading (M6) request/response schemas.

Deployment is **version-pinned**: the admission gates are evaluated per frozen
strategy version, so ``version`` is required (no working-copy deploy).  The
wizard's final step carries the disclaimer ack + account-tail confirmation
(PRD: 免责条款勾选 + 二次确认输入账户尾号，均不可跳过).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RunStatus = Literal["pending", "running", "paused", "stopping", "stopped", "failed"]
Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
RuleType = Literal[
    "daily_max_loss", "daily_max_loss_pct", "max_drawdown", "max_lot_per_order",
    "max_open_positions", "max_exposure", "max_orders_per_minute",
    "consecutive_loss_cooldown", "order_rate_window",
]
RuleAction = Literal["reject", "halt_open_today", "close_all_and_stop"]
RuleApplyTo = Literal["ai", "manual", "both"]


class AdmissionCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: uuid.UUID
    version: int = Field(ge=1, description="冻结版本号（准入以版本为维度）")
    account_id: uuid.UUID


class GateOut(BaseModel):
    key: str
    label: str
    passed: bool
    reason: str
    metrics: dict = Field(default_factory=dict)


class AdmissionOut(BaseModel):
    strategy_id: str
    strategy_version: int
    account_id: str
    allowed: bool
    gates: list[GateOut]
    decision_metrics: dict = Field(default_factory=dict)


class RuleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: RuleType
    threshold: float = Field(gt=0)
    action: RuleAction = "reject"

    @model_validator(mode="after")
    def _check(self) -> "RuleSpec":
        if self.type in ("max_open_positions", "max_orders_per_minute") and \
                float(self.threshold) != int(self.threshold):
            raise ValueError(f"{self.type} 阈值必须是整数（笔/个）")
        return self


class LiveCreate(BaseModel):
    """Final wizard step: deploy the frozen version to the bound account."""

    model_config = ConfigDict(extra="forbid")

    strategy_id: uuid.UUID
    version: int = Field(ge=1)
    account_id: uuid.UUID
    scope: Literal["demo", "real"] = Field(
        default="real",
        description="demo=模拟盘交易（免准入闸，订单发往 MT5 模拟服务器）；real=实盘（全闸）",
    )
    symbol: str = Field(min_length=1, max_length=32)
    timeframe: Timeframe
    allocated_capital: float = Field(gt=0, le=1e12, description="分配给该策略的资金上限")
    leverage: int = Field(ge=1, le=1000)
    warmup_bars: int = Field(default=100, ge=0, le=1000)
    parameters: dict[str, object] = Field(default_factory=dict)
    rules: list[RuleSpec] = Field(min_length=1, description="风控规则（强制，至少一条）")
    observation_days: int | None = Field(default=None, ge=1, le=30,
                                         description="观察期天数，None=平台默认")
    disclaimer_ack: bool = Field(
        description="必须为 true：已确认「平台风控依赖网关在线，掉线期间持仓无保护」"
    )
    account_tail: str = Field(
        min_length=4, max_length=4, description="二次确认：输入 MT5 账号尾号 4 位"
    )

    @model_validator(mode="after")
    def _check(self) -> "LiveCreate":
        if not self.disclaimer_ack:
            raise ValueError("部署实盘前必须勾选免责条款确认")
        kinds = {r.type for r in self.rules}
        if not kinds & {"daily_max_loss", "daily_max_loss_pct", "max_drawdown"}:
            raise ValueError("风控规则必须至少包含一条亏损/回撤类规则（PRD 强制）")
        return self


class LiveStopRequest(BaseModel):
    close_positions: bool = Field(
        default=True, description="true = 停止时通过网关平掉该策略全部持仓"
    )


class KillSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: str = Field(
        min_length=1, description="二次确认串，必须为 KILL"
    )


class ReAdmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_note: str = Field(min_length=8, max_length=500,
                             description="人工复核意见（审计留痕）")
    confirm: str = Field(min_length=1, description="二次确认串，必须为 READMIT")


class LiveRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    strategy_id: uuid.UUID
    strategy_version: int | None
    account_id: uuid.UUID
    account_type: str | None = None  # demo|real|contest（列表页区分 模拟盘交易/实盘）
    status: RunStatus
    symbol: str
    timeframe: str
    execution_mode: str
    allocated_capital: float
    leverage: int
    magic: int
    warmup_bars: int
    parameters: dict[str, object]
    error_code: str | None
    error_message: str | None
    observation_ends_at: datetime | None
    capital_cap_pct: int
    lot_cap: float | None
    no_new_orders: bool
    reconcile_pending: bool
    demoted: bool
    balance: float
    equity: float
    margin_used: float
    margin_free: float
    unrealized_pnl: float
    last_bar_time: datetime | None
    started_at: datetime | None
    stopped_at: datetime | None
    created_at: datetime


class LiveRunDetailOut(LiveRunOut):
    config_snapshot: dict
    admission_snapshot: dict
    positions: list[dict]
    equity_history: list[dict]
    # 子进程 ctx.storage 快照（storage.json，best-effort）——网格锚定价等策略状态
    strategy_state: dict | None = None


class LiveListResponse(BaseModel):
    items: list[LiveRunOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class LiveTradeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    deal_ticket: int
    order_ticket: int
    position_id: str
    direction: Literal["buy", "sell"]
    entry: str
    volume: float
    open_time: datetime
    price: float
    close_time: datetime | None
    profit: float
    swap: float
    commission: float
    retcode: int
    comment: str
    is_forced: bool


class LiveTradeListResponse(BaseModel):
    items: list[LiveTradeOut]
    total: int


class LiveLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    seq: int
    bar_time: datetime | None
    kind: Literal["log", "signal", "system", "risk"]
    level: str
    message: str
    payload: dict | None


class LiveLogListResponse(BaseModel):
    items: list[LiveLogOut]
    total: int


# ── risk center: rules / alerts / account unfreeze ─────────────

class RiskRuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["global", "account", "run"] = "account"
    account_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    type: RuleType
    threshold: float = Field(gt=0)
    cooldown_minutes: float | None = Field(
        default=None, gt=0, description="consecutive_loss_cooldown：休息分钟数"
    )
    window_minutes: float | None = Field(
        default=None, gt=0, description="order_rate_window：滚动窗口分钟数"
    )
    apply_to: RuleApplyTo = "both"
    action: RuleAction = "reject"
    note: str = Field(default="", max_length=200)


class RiskRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scope: str
    account_id: uuid.UUID | None
    run_id: uuid.UUID | None
    type: RuleType
    threshold: float
    cooldown_minutes: float | None = None
    window_minutes: float | None = None
    apply_to: RuleApplyTo = "both"
    action: RuleAction
    enabled: bool
    note: str
    created_at: datetime


class RiskRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: float | None = Field(default=None, gt=0)
    cooldown_minutes: float | None = Field(default=None, gt=0)
    window_minutes: float | None = Field(default=None, gt=0)
    apply_to: RuleApplyTo | None = None
    action: RuleAction | None = None
    enabled: bool | None = None
    note: str | None = Field(default=None, max_length=200)


class RiskEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID | None
    account_id: uuid.UUID | None
    rule_type: str
    source: str
    threshold: float
    observed: float
    action_taken: str
    detail: dict
    created_at: datetime


class RiskEventListResponse(BaseModel):
    items: list[RiskEventOut]
    total: int


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    level: str
    source: str
    title: str
    message: str
    account_id: uuid.UUID | None
    run_id: uuid.UUID | None
    is_read: bool
    created_at: datetime


class AlertListResponse(BaseModel):
    items: list[AlertOut]
    total: int
    unread: int


class UnfreezeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_note: str = Field(min_length=8, max_length=500,
                             description="解冻复核意见（审计留痕）")
    confirm: str = Field(min_length=1, description="二次确认串，必须为 UNFREEZE")
