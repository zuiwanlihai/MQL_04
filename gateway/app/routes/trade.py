"""POST /trade/order · /trade/close · /trade/cancel · GET /trade/positions|orders|deals.

Live execution surface (M6).  Every command runs on the single MT5 worker via
``client.run``; trade commands use ``PRIORITY_TRADE`` so a Kill Switch is never
stuck behind a queued market poll.  The server-side gate (feature flag + login
whitelist + algo-trade switches) lives in ``MT5Client._check_trade_allowed_sync``.
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import add_trade_allowed_login, remove_trade_allowed_login, settings
from app.mt5_client import client
from app.schemas import (
    CancelRequest,
    CloseRequest,
    OrderRequest,
    PendingRequest,
    TradeResult,
    WhitelistIn,
    WhitelistOut,
)
from app.security import require_gateway_key

router = APIRouter(prefix="/trade", dependencies=[Depends(require_gateway_key)])


@router.get("/whitelist", response_model=WhitelistOut)
async def whitelist_get() -> dict:
    """Current trade-whitelist state (feature flag + allowed logins)."""
    return {
        "trade_enabled": settings.TRADE_ENABLED,
        "logins": sorted(settings.trade_allowed_logins),
    }


@router.post("/whitelist", response_model=WhitelistOut)
async def whitelist_add(payload: WhitelistIn) -> dict:
    """Add an MT5 login to the trade whitelist (runtime + persisted to .env)."""
    if not settings.TRADE_ENABLED:
        raise HTTPException(status_code=409, detail="网关交易开关未开启（TRADE_ENABLED=false）")
    logins = add_trade_allowed_login(payload.login)
    return {"trade_enabled": True, "logins": sorted(logins)}


@router.delete("/whitelist/{login}", response_model=WhitelistOut)
async def whitelist_remove(login: int) -> dict:
    """Remove an MT5 login from the trade whitelist (runtime + persisted)."""
    logins = remove_trade_allowed_login(login)
    return {"trade_enabled": settings.TRADE_ENABLED, "logins": sorted(logins)}


@router.post("/order", response_model=TradeResult)
async def order(payload: OrderRequest) -> dict:
    """Market buy/sell on the bound account. 409 when trading is disallowed."""
    return await client.place_market(
        payload.symbol,
        payload.volume,
        payload.side,
        magic=payload.magic,
        comment=payload.comment,
        deviation=payload.deviation,
    )


@router.post("/pending", response_model=TradeResult)
async def pending(payload: PendingRequest) -> dict:
    """Place a limit/stop pending order. 409 when trading is disallowed."""
    return await client.place_pending(
        payload.symbol,
        payload.volume,
        payload.order_type,
        payload.price,
        sl=payload.sl,
        tp=payload.tp,
        magic=payload.magic,
        comment=payload.comment,
    )


@router.post("/close", response_model=TradeResult)
async def close(payload: CloseRequest) -> dict:
    """Close (part of) an open position by ticket; volume=None closes all."""
    return await client.close_position(
        payload.ticket, payload.volume, magic=payload.magic,
        deviation=payload.deviation,
    )


@router.post("/cancel", response_model=TradeResult)
async def cancel(payload: CancelRequest) -> dict:
    """Cancel a pending order by ticket."""
    return await client.cancel_order(payload.ticket)


@router.get("/positions")
async def positions(
    symbol: Annotated[str | None, Query(description="按品种过滤，留空=全部")] = None,
) -> list[dict]:
    """Open positions on the account (live mirror for reconciliation)."""
    return await client.positions(symbol)


@router.get("/orders")
async def orders(
    symbol: Annotated[str | None, Query(description="按品种过滤，留空=全部")] = None,
) -> list[dict]:
    """Active (pending) orders."""
    return await client.orders(symbol)


@router.get("/deals")
async def deals(
    start: Annotated[int, Query(ge=0, description="起始时间（epoch 秒，含）")],
    end: Annotated[int | None, Query(ge=0, description="结束时间（epoch 秒，含）")] = None,
    symbol: Annotated[str | None, Query(description="按品种过滤")] = None,
    position: Annotated[
        int | None, Query(ge=1, description="按持仓票号查成交（历史未同步时可靠）")
    ] = None,
) -> list[dict]:
    """Executed deals in [start, end] (or all deals of one position); end defaults to now."""
    return await client.deals(start, end if end is not None else int(time.time()), symbol,
                              position=position)
