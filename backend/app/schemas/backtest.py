"""Backtest center (M4) request/response schemas.

Mirrors ``frontend/src/types/api.ts``.  ``assumption_ack`` must be true on
submission (PRD M4 提交前显性确认, P0); ``sensitivity`` creates a paired
conservative + standard run sharing one ``group_id`` (optimistic mode may
not be paired).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

BacktestStatus = Literal[
    "pending", "running", "cancelling", "completed", "failed", "cancelled"
]
ExecutionMode = Literal["conservative", "standard", "optimistic"]
SpreadMode = Literal["current", "fixed"]
Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]

_LOG_LIMIT = 5000


class LocalBarIn(BaseModel):
    """本地缓存 K 线单根（data_source=local 时随请求上传）。"""

    time: int = Field(gt=0, description="bar 开始时间，epoch 秒（UTC）")
    open: float
    high: float
    low: float
    close: float
    tick_volume: int = Field(default=0, ge=0)
    spread: int = Field(default=0, ge=0)


class BacktestCreate(BaseModel):
    strategy_id: uuid.UUID
    version: int | None = Field(
        default=None,
        description="冻结版本号；None = 提交时冻结当前工作副本",
    )
    symbol: str = Field(min_length=1, max_length=32)
    timeframe: Timeframe
    start_date: datetime = Field(description="回放窗口起点（UTC，含）")
    end_date: datetime = Field(description="回放窗口终点（UTC，不含）")
    data_source: Literal["server", "local"] = Field(
        default="server",
        description="K 线来源：server=网关历史，local=浏览器本地缓存K线（local_bars 必传）",
    )
    local_bars: list[LocalBarIn] | None = Field(
        default=None,
        description="data_source=local 时必传：浏览器 localStorage 缓存的K线序列",
    )
    initial_deposit: float = Field(gt=0, le=1e12)
    leverage: int = Field(ge=1, le=1000)
    execution_mode: ExecutionMode = "conservative"
    spread_mode: SpreadMode = "current"
    spread_points: float | None = Field(default=None, ge=0, le=10000)
    commission_per_lot: float = Field(default=0.0, ge=0, le=10000)
    slippage_points: float | None = Field(default=None, ge=0, le=1000)
    warmup_bars: int = Field(default=100, ge=0, le=1000)
    parameters: dict[str, object] = Field(default_factory=dict)
    assumption_ack: bool = Field(
        description="必须为 true：已确认本档位的成交价构成假设（PRD P0）"
    )
    sensitivity: bool = Field(
        default=False,
        description="true = 同配置成对创建保守+标准两任务（共享 group_id）",
    )

    @model_validator(mode="after")
    def _check(self) -> "BacktestCreate":
        if not self.assumption_ack:
            raise ValueError("提交回测前必须确认撮合档位的成交价构成假设")
        if self.spread_mode == "fixed" and self.spread_points is None:
            raise ValueError("固定点差模式必须提供 spread_points")
        if self.sensitivity and self.execution_mode == "optimistic":
            raise ValueError("敏感性对比仅支持保守/标准档配对")
        if self.start_date >= self.end_date:
            raise ValueError("起始时间必须早于结束时间")
        return self


class BacktestTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    strategy_id: uuid.UUID
    strategy_version: int | None
    status: BacktestStatus
    execution_mode: ExecutionMode
    symbol: str
    timeframe: str
    start_date: datetime
    end_date: datetime
    initial_deposit: float
    leverage: int
    spread_mode: SpreadMode
    spread_points: float | None
    commission_per_lot: float
    slippage_points: float | None
    warmup_bars: int
    parameters: dict[str, object]
    group_id: uuid.UUID | None
    progress_percent: int
    error_code: str | None
    error_message: str | None
    timed_out: bool
    rating: int | None
    data_source: str
    started_at: datetime | None
    completed_at: datetime | None
    created_by: uuid.UUID | None
    created_at: datetime


class BacktestRatingRequest(BaseModel):
    """打分: 1-5 星; null 清除评分。"""

    rating: int | None = Field(default=None, ge=1, le=5)


class BacktestTaskDetailOut(BacktestTaskOut):
    """Detail response adds the frozen config snapshot (assumption surface)."""

    config_snapshot: dict


class BacktestListResponse(BaseModel):
    items: list[BacktestTaskOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class EquityPoint(BaseModel):
    time: str
    equity: float
    balance: float
    drawdown: float
    baseline: float


class BacktestReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    total_return: float
    total_return_percent: float
    annual_return_percent: float
    annual_volatility_percent: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    recovery_factor: float
    max_drawdown_percent: float
    max_drawdown_amount: float
    win_rate: float
    profit_factor: float
    expectancy: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win: float
    avg_loss: float
    avg_hold_hours: float
    consecutive_wins: int
    consecutive_losses: int
    total_swap: float
    equity_curve: list[dict]
    monthly_returns: dict[str, float]
    generated_at: datetime


class BacktestTradeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    ticket: int
    direction: Literal["buy", "sell"]
    volume: float
    open_time: datetime
    open_price: float
    base_price: float
    close_time: datetime | None
    close_price: float | None
    profit: float
    profit_percent: float
    swap: float
    commission: float
    spread_cost: float
    slippage_cost: float
    comment: str
    is_forced: bool


class TradeListResponse(BaseModel):
    items: list[BacktestTradeOut]
    total: int


class BacktestLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    seq: int
    bar_time: datetime | None
    kind: Literal["log", "signal", "system"]
    level: str
    message: str
    payload: dict | None


class LogListResponse(BaseModel):
    items: list[BacktestLogOut]
    total: int
    truncated: bool = Field(
        default=False, description="日志超过 5000 行上限被截断时为 True"
    )


class SensitivitySide(BaseModel):
    task_id: uuid.UUID
    execution_mode: ExecutionMode
    status: BacktestStatus
    annual_return_percent: float | None = None
    max_drawdown_percent: float | None = None
    sharpe_ratio: float | None = None
    total_return_percent: float | None = None


class SensitivityCompareOut(BaseModel):
    group_id: uuid.UUID
    conservative: SensitivitySide
    standard: SensitivitySide
    annual_diff_percent: float | None = Field(
        default=None, description="保守档 - 标准档 年化收益差（百分点）"
    )
    sensitive: bool = Field(
        default=False, description="年化差 > 5 个百分点 → 执行价格敏感告警"
    )


# ---------------------------------------------------------------------------
# Overfit-guard analyses (Walk-Forward / Monte Carlo, PRD Phase 2)
# ---------------------------------------------------------------------------

AnalysisKind = Literal["walk_forward", "monte_carlo", "optimization"]
AnalysisStatus = Literal["running", "completed", "failed"]
MCMethod = Literal["bootstrap", "shuffle"]


class WalkForwardCreate(BaseModel):
    folds: int = Field(default=4, ge=2, le=8, description="折数（每折 IS+OOS 两个子回测）")
    mode: Literal["rolling", "anchored"] = Field(
        default="rolling", description="rolling=滚动窗口；anchored=扩张窗口"
    )


class MonteCarloCreate(BaseModel):
    simulations: int = Field(default=1000, ge=100, le=10000)
    method: MCMethod = Field(default="bootstrap", description="bootstrap=有放回；shuffle=打乱顺序")
    ruin_dd_percent: float = Field(
        default=30.0, ge=1, le=100, description="破产线：最大回撤超过该值（%）"
    )
    seed: int | None = Field(
        default=None, ge=0, le=2**31 - 1, description="留空则随机（结果可复现）"
    )


class AnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    kind: AnalysisKind
    status: AnalysisStatus
    params: dict[str, object]
    result: dict[str, object] | None
    error: str | None
    created_at: datetime
    updated_at: datetime | None


class AnalysisListResponse(BaseModel):
    items: list[AnalysisOut]
    total: int


class OptimizationParamSpace(BaseModel):
    """One optimisable parameter: name + candidate values (2–10)."""

    name: str = Field(
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", min_length=1, max_length=32,
        description="参数名（合法 Python 标识符，与策略 params 键一致）",
    )
    values: list[Union[int, float, str, bool]] = Field(
        min_length=2, max_length=10,
        description="候选值（2~10 个；bool/数字/字符串按策略声明类型传递）",
    )


OptimizationObjective = Literal[
    "sharpe_ratio", "annual_return_percent", "total_return_percent",
    "calmar_ratio", "profit_factor",
]


class OptimizationCreate(BaseModel):
    """Grid / random parameter search off a completed backtest task."""

    space: list[OptimizationParamSpace] = Field(
        min_length=1, max_length=4, description="参数空间（1~4 个参数）"
    )
    method: Literal["grid", "random"] = Field(
        default="grid", description="grid=网格全组合；random=随机采样"
    )
    samples: int = Field(
        default=20, ge=1, le=100,
        description="随机采样次数（仅 method=random，≤ 组合总数）",
    )
    objective: OptimizationObjective = Field(
        default="sharpe_ratio", description="排名目标指标（降序）"
    )
    seed: int | None = Field(
        default=None, ge=0, le=2**31 - 1,
        description="随机采样种子；留空则随机（结果可复现）",
    )

    @model_validator(mode="after")
    def _check(self) -> "OptimizationCreate":
        names = [p.name for p in self.space]
        if len(set(names)) != len(names):
            raise ValueError("参数名重复")
        total = 1
        for p in self.space:
            total *= len(p.values)
        if total > 100:
            raise ValueError(f"网格组合总数 {total} 超过上限 100，请缩减参数空间")
        if self.method == "random" and self.samples > total:
            raise ValueError(
                f"随机采样次数 {self.samples} 超过组合总数 {total}，"
                "请降低 samples 或扩大参数空间"
            )
        return self


# ---------------------------------------------------------------------------
# Portfolio backtest (multi-symbol, PRD Phase 3 P2)
# ---------------------------------------------------------------------------

PortfolioStatus = Literal["running", "completed", "failed"]


class PortfolioCreate(BaseModel):
    """One strategy replayed across N symbols with equal-weight deposits."""

    strategy_id: uuid.UUID
    symbols: list[str] = Field(
        min_length=2, max_length=8, description="品种列表（2~8 个，去重）"
    )
    timeframe: Timeframe
    start_date: datetime = Field(description="回放窗口起点（UTC，含）")
    end_date: datetime = Field(description="回放窗口终点（UTC，不含）")
    total_deposit: float = Field(gt=0, le=1e12, description="总资金（均分到每个品种）")
    leverage: int = Field(ge=1, le=1000)
    execution_mode: ExecutionMode = "conservative"
    spread_mode: SpreadMode = "current"
    spread_points: float | None = Field(default=None, ge=0, le=10000)
    commission_per_lot: float = Field(default=0.0, ge=0, le=10000)
    slippage_points: float | None = Field(default=None, ge=0, le=1000)
    warmup_bars: int = Field(default=100, ge=0, le=1000)
    assumption_ack: bool = Field(description="必须为 true：已确认撮合档位假设")

    @model_validator(mode="after")
    def _check(self) -> "PortfolioCreate":
        if not self.assumption_ack:
            raise ValueError("提交组合回测前必须确认撮合档位的成交价构成假设")
        if self.spread_mode == "fixed" and self.spread_points is None:
            raise ValueError("固定点差模式必须提供 spread_points")
        if self.start_date >= self.end_date:
            raise ValueError("起始时间必须早于结束时间")
        cleaned = [s.strip() for s in self.symbols]
        if any(not s or len(s) > 32 for s in cleaned):
            raise ValueError("品种名不能为空且长度 ≤ 32")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("品种列表存在重复")
        self.symbols = cleaned
        return self


class PortfolioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    strategy_id: uuid.UUID
    strategy_name: str = ""
    symbols: list[str]
    timeframe: str
    total_deposit: float
    status: PortfolioStatus
    params: dict[str, object]
    result: dict[str, object] | None
    error: str | None
    created_at: datetime
    updated_at: datetime | None


class PortfolioListResponse(BaseModel):
    items: list[PortfolioOut]
    total: int
    page: int
    page_size: int
    total_pages: int
