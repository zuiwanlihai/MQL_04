"""Manual trading panel (M14) request/response schemas.

Mirrors ``frontend/src/types/api.ts``.  Manual orders touch REAL money: every
action is gated by the pre-trade risk engine + written to the audit chain, and
the account is addressed through its bound gateway node (M12 routing).

Market orders go through ``POST /trade/order``; limit/stop pending orders
through ``POST /trade/pending`` (both risk-gated + audited the same way).
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class ManualOrderRequest(BaseModel):
    """POST /trade/order — market buy/sell on a bound account."""

    account_id: uuid.UUID
    symbol: str = Field(min_length=1, max_length=32)
    side: Literal["buy", "sell"]
    volume: float = Field(gt=0, le=1000, description="手数（网关按品种步长归一化）")
    deviation: int = Field(default=20, ge=0, le=1000, description="最大滑点 points")
    comment: str = Field(default="", max_length=31)


class ManualPendingRequest(BaseModel):
    """POST /trade/pending — place a limit/stop pending order."""

    account_id: uuid.UUID
    symbol: str = Field(min_length=1, max_length=32)
    order_type: Literal["buy_limit", "sell_limit", "buy_stop", "sell_stop"]
    volume: float = Field(gt=0, le=1000, description="手数（网关按品种步长归一化）")
    price: float = Field(gt=0, description="挂单触发价")
    sl: float | None = Field(default=None, gt=0, description="止损价（可选）")
    tp: float | None = Field(default=None, gt=0, description="止盈价（可选）")
    comment: str = Field(default="", max_length=31)


class ManualCloseRequest(BaseModel):
    """POST /trade/close — close (part of) an open position by ticket."""

    account_id: uuid.UUID
    ticket: int = Field(ge=1, description="持仓票号")
    volume: float | None = Field(default=None, gt=0, le=1000,
                                 description="None = 全平该持仓")


class ManualCancelRequest(BaseModel):
    """POST /trade/cancel — cancel a pending order by ticket."""

    account_id: uuid.UUID
    ticket: int = Field(ge=1)


class WhitelistAddIn(BaseModel):
    """POST /trade/whitelist — add an MT5 login to the gateway trade whitelist."""

    login: int = Field(ge=1, description="MT5 账号")


class TradeActionResult(BaseModel):
    """Uniform write-action response (order / close / cancel)."""

    success: bool
    action: Literal["order", "pending", "close", "cancel"]
    retcode: int | None = None
    retcode_text: str | None = None
    order: int | None = None
    deal: int | None = None
    price: float | None = None
    volume: float | None = None
    error_code: str | None = None
    error_message: str | None = None
    # post-action read-only mirror so the panel refreshes without a second round-trip
    account: dict | None = None
    positions: list[dict] | None = None


class TradeSnapshot(BaseModel):
    """One login round-trip returning the account mirror for the panel."""

    account: dict | None = None
    positions: list[dict] = Field(default_factory=list)
    orders: list[dict] = Field(default_factory=list)
