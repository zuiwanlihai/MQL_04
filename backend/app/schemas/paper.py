"""Paper trading (M5) request/response schemas.

Mirrors ``frontend/src/types/api.ts``.  ``assumption_ack`` must be true on
start (same PRD P0 rule as backtest submission).  Paper runs only accept the
``standard`` / ``optimistic`` tiers — the conservative tier needs the *next*
bar's open price which an incremental live loop cannot know.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PaperRunStatus = Literal["pending", "running", "paused", "stopping", "stopped", "failed"]
PaperExecutionMode = Literal["standard", "optimistic"]
SpreadMode = Literal["current", "fixed"]
Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


class PaperCreate(BaseModel):
    strategy_id: uuid.UUID
    account_id: uuid.UUID | None = Field(
        default=None,
        description="gateway 模式必填：绑定的 MT5 账户（M12）；synthetic 模式忽略",
    )
    data_source: Literal["gateway", "synthetic"] = Field(
        default="gateway",
        description="行情来源：gateway=网关实时行情；synthetic=合成数据集回放",
    )
    synthetic_dataset_id: int | None = Field(
        default=None, gt=0,
        description="data_source=synthetic 时：SyntheticDataset 主键",
    )
    version: int | None = Field(
        default=None,
        description="冻结版本号；None = 启动时冻结当前工作副本",
    )
    symbol: str = Field(min_length=1, max_length=32)
    timeframe: Timeframe
    initial_deposit: float = Field(gt=0, le=1e12)
    leverage: int = Field(ge=1, le=1000)
    execution_mode: PaperExecutionMode = "standard"
    spread_mode: SpreadMode = "current"
    spread_points: float | None = Field(default=None, ge=0, le=10000)
    commission_per_lot: float = Field(default=0.0, ge=0, le=10000)
    slippage_points: float | None = Field(default=None, ge=0, le=1000)
    warmup_bars: int = Field(default=100, ge=0, le=1000)
    parameters: dict[str, object] = Field(default_factory=dict)
    assumption_ack: bool = Field(
        description="必须为 true：已确认撮合档位的成交价构成假设（PRD P0）"
    )

    @model_validator(mode="after")
    def _check(self) -> "PaperCreate":
        if not self.assumption_ack:
            raise ValueError("启动虚拟下单前必须确认撮合档位的成交价构成假设")
        if self.spread_mode == "fixed" and self.spread_points is None:
            raise ValueError("固定点差模式必须提供 spread_points")
        if self.data_source == "gateway":
            if self.account_id is None:
                raise ValueError("网关模式必须提供绑定的 MT5 账户 account_id")
        elif self.data_source == "synthetic":
            if self.synthetic_dataset_id is None:
                raise ValueError("合成数据集模式必须提供 synthetic_dataset_id")
        return self


class PaperStopRequest(BaseModel):
    close_positions: bool = Field(
        default=True, description="true = 停止时按最后收盘价平掉全部持仓"
    )


class PaperRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    strategy_id: uuid.UUID
    account_id: uuid.UUID | None
    strategy_version: int | None
    data_source: Literal["gateway", "synthetic"]
    synthetic_dataset_id: int | None
    status: PaperRunStatus
    execution_mode: PaperExecutionMode
    symbol: str
    timeframe: str
    initial_deposit: float
    leverage: int
    spread_mode: SpreadMode
    spread_points: float | None
    commission_per_lot: float
    slippage_points: float | None
    warmup_bars: int
    parameters: dict[str, object]
    error_code: str | None
    error_message: str | None
    balance: float
    equity: float
    margin_used: float
    margin_free: float
    unrealized_pnl: float
    last_bar_time: datetime | None
    started_at: datetime | None
    stopped_at: datetime | None
    created_at: datetime


class PaperRunDetailOut(PaperRunOut):
    """Detail response adds the frozen config snapshot and open positions."""

    config_snapshot: dict
    positions: list[dict]
    equity_history: list[dict]
    # 子进程 ctx.storage 快照（storage.json，best-effort）——网格锚定价等策略状态
    strategy_state: dict | None = None


class PaperListResponse(BaseModel):
    items: list[PaperRunOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class PaperTradeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    ticket: int
    direction: Literal["buy", "sell"]
    volume: float
    open_time: datetime
    open_price: float
    base_price: float
    close_time: datetime
    close_price: float
    profit: float
    profit_percent: float
    swap: float
    commission: float
    spread_cost: float
    slippage_cost: float
    comment: str
    is_forced: bool
    # 产生于被熔断打断的回调内：这笔成交本身可疑，别拿它当可信样本。
    suspect: bool = False


class TradeListResponse(BaseModel):
    items: list[PaperTradeOut]
    total: int


class PaperLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    seq: int
    bar_time: datetime | None
    kind: Literal["log", "signal", "system"]
    level: str
    message: str
    payload: dict | None


class LogListResponse(BaseModel):
    items: list[PaperLogOut]
    total: int
