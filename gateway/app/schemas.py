"""Pydantic request/response models for the gateway routes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """POST /account/login payload."""

    model_config = ConfigDict(extra="forbid")

    login: int = Field(..., ge=1, description="MT5 账号")
    password: str = Field(..., min_length=1, description="MT5 密码")
    server: str = Field(..., min_length=1, description="经纪商服务器")


class AccountSummary(BaseModel):
    """Account snapshot returned by login / account probes."""

    model_config = ConfigDict(extra="forbid")

    login: int
    server: str
    name: str
    type: str  # demo | real | contest
    leverage: int
    currency: str
    balance: float
    credit: float
    equity: float
    margin: float
    margin_free: float
    margin_level: float
    trade_allowed: bool


class TerminalInfo(BaseModel):
    """Terminal connectivity block inside /health."""

    model_config = ConfigDict(extra="forbid")

    connected: bool
    trade_allowed: bool


class HealthResponse(BaseModel):
    """GET /health payload — must always be answerable."""

    model_config = ConfigDict(extra="forbid")

    status: str  # ok | degraded | no_login | no_terminal
    version: str
    mt5_version: str
    uptime_seconds: float
    cpu_percent: float
    memory_percent: float
    active_sessions: int
    terminal: TerminalInfo
    account: AccountSummary | None = None
    error: str | None = None


class WhitelistIn(BaseModel):
    """POST /trade/whitelist — add an MT5 login to the trade whitelist."""

    model_config = ConfigDict(extra="forbid")

    login: int = Field(..., ge=1, description="MT5 账号")


class WhitelistOut(BaseModel):
    """GET/POST /trade/whitelist payload."""

    model_config = ConfigDict(extra="forbid")

    trade_enabled: bool
    logins: list[int]


# -- trading (M6) --------------------------------------------------------------
class OrderRequest(BaseModel):
    """POST /trade/order — market order on the bound MT5 account."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, max_length=32)
    volume: float = Field(..., gt=0, description="请求手数（网关按品种步长归一化）")
    side: str = Field(..., pattern="^(buy|sell)$")
    magic: int = Field(default=0, ge=0, description="策略/运行标识，用于对账归属")
    comment: str = Field(default="", max_length=31)
    deviation: int = Field(default=20, ge=0, description="最大允许滑点 points")


class CloseRequest(BaseModel):
    """POST /trade/close — close (part of) an open position."""

    model_config = ConfigDict(extra="forbid")

    ticket: int = Field(..., ge=1, description="持仓票号")
    volume: float | None = Field(default=None, gt=0, description="None=全平")
    magic: int = Field(default=0, ge=0)
    deviation: int = Field(default=20, ge=0)


class PendingRequest(BaseModel):
    """POST /trade/pending — place a limit/stop pending order."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, max_length=32)
    volume: float = Field(..., gt=0, description="请求手数（网关按品种步长归一化）")
    order_type: str = Field(..., pattern="^(buy_limit|sell_limit|buy_stop|sell_stop)$")
    price: float = Field(..., gt=0, description="挂单触发价")
    sl: float | None = Field(default=None, gt=0, description="止损价（可选）")
    tp: float | None = Field(default=None, gt=0, description="止盈价（可选）")
    magic: int = Field(default=0, ge=0)
    comment: str = Field(default="", max_length=31)


class CancelRequest(BaseModel):
    """POST /trade/cancel — cancel a pending order."""

    model_config = ConfigDict(extra="forbid")

    ticket: int = Field(..., ge=1)


class TradeResult(BaseModel):
    """Uniform response for every order_send call.

    ``bid``/``ask`` snapshot the broker quote taken *at request time* (None
    for order types without a pre-quote, e.g. cancel) so callers can measure
    real execution slippage as the gap between that quote and the deal price.
    """

    model_config = ConfigDict(extra="forbid")

    retcode: int
    success: bool
    retcode_text: str
    deal: int
    order: int
    price: float
    volume: float
    comment: str
    bid: float | None = None
    ask: float | None = None


class TerminalAlgoRequest(BaseModel):
    """POST /terminal/algo-trading — 拨动终端本体「算法交易」按钮。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(..., description="true=开启终端 AutoTrading；false=关闭")


class TerminalAlgoResult(BaseModel):
    """终端按钮切换结果（状态已复读验证）。"""

    model_config = ConfigDict(extra="forbid")

    changed: bool = Field(description="本次是否实际发生了切换（目标状态已达标时为 false）")
    trade_allowed: bool = Field(description="切换后的终端 AutoTrading 状态")
    message: str = ""
