"""
Account binding (M1) request/response schemas.

Mirrors ``frontend/src/types/api.ts``.  ``password_encrypted`` is
structurally excluded — the plaintext password never leaves the client and
the ciphertext never leaves the server.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AccountCredentials(BaseModel):
    """Body shared by POST /accounts/test and POST /accounts."""

    gateway_url: str | None = Field(
        default=None, description="可选，覆盖默认网关地址（测试连接用）"
    )
    gateway_node_id: uuid.UUID | None = Field(
        default=None,
        description="可选，绑定到指定网关节点（M13：虚拟下单/实盘流程内直接绑定，"
        "登录探测与后续行情/交易都路由到该节点）",
    )
    login: int = Field(ge=1, description="MT5 账号")
    password: str = Field(min_length=1, max_length=128)
    server: str = Field(min_length=1, max_length=128, description="券商服务器名")
    remember_password: bool = Field(
        default=True,
        description="绑定后是否记住密码（Fernet 加密存储）；不记住则每次切换登录需重新输入",
    )


class AccountProbeResult(BaseModel):
    """Gateway login summary — returned by the test endpoint (never stored)."""

    model_config = ConfigDict(extra="forbid")

    login: int
    server: str
    name: str
    account_type: Literal["demo", "real", "contest"]
    leverage: int
    currency: str
    balance: float
    credit: float
    equity: float
    margin: float
    margin_free: float
    margin_level: float
    is_trade_allowed: bool


class MT5AccountOut(BaseModel):
    """Mirrors the frontend ``MT5Account`` type (explicit fields only)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    login: int
    server: str
    name: str
    account_type: Literal["demo", "real", "contest"]
    leverage: int
    currency: str
    balance: float
    credit: float
    equity: float
    margin: float
    margin_free: float
    margin_level: float
    is_connected: bool
    is_trade_allowed: bool
    trade_frozen: bool = False
    live_allowed: bool = False
    algo_trading_enabled: bool = True
    gateway_node_id: uuid.UUID
    has_credentials: bool = False
    last_sync_at: datetime
    created_at: datetime
    updated_at: datetime | None = None


class AccountListResponse(BaseModel):
    items: list[MT5AccountOut]
    total: int


class ActiveAccountInfo(BaseModel):
    """One gateway node's currently logged-in MT5 account (live probe)."""

    model_config = ConfigDict(extra="forbid")

    node_id: uuid.UUID
    node_name: str
    account: AccountProbeResult | None = None


class ActiveAccountListResponse(BaseModel):
    items: list[ActiveAccountInfo]


class ActivateRequest(BaseModel):
    """POST /accounts/{id}/activate 的可选 body。

    绑定时未记住密码的账户：切换登录时必须携带本次密码（仅用于本次
    网关 mt5.login，不落库）。
    """

    password: str | None = Field(default=None, min_length=1, max_length=128)


class AlgoTradingRequest(BaseModel):
    """POST /accounts/{id}/algo-trading 的 body（允许算法交易开关）。"""

    enabled: bool = Field(description="true=允许算法交易；false=拒绝该账户的策略自动下单")


class TerminalSyncInfo(BaseModel):
    """MT5 终端本体「算法交易」按钮（AutoTrading）同步结果。"""

    model_config = ConfigDict(extra="forbid")

    ok: bool = Field(description="终端按钮是否已同步到目标状态")
    trade_allowed: bool | None = Field(
        default=None, description="同步后的终端 AutoTrading 状态（未知为 null）"
    )
    message: str | None = Field(default=None, description="面向用户的补充说明/失败原因")


class AlgoTradingResponse(BaseModel):
    """POST /accounts/{id}/algo-trading 响应：账户开关 + 终端按钮同步结果。"""

    account: MT5AccountOut
    terminal_sync: TerminalSyncInfo
