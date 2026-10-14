"""Manual trading panel endpoints (M14).

Real-money surface: every write action delegates to
:mod:`app.services.manual_trade` (pre-trade risk gate + audit + M12 node
routing).  Read endpoints (snapshot / deals) log in to the account's node and
mirror the broker truth — ownership is enforced (foreign account → 404).
"""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, HTTPException, Query, Request

from app.dependencies import CurrentUser, DBSession
from app.schemas.trade import (
    ManualCancelRequest,
    ManualCloseRequest,
    ManualOrderRequest,
    ManualPendingRequest,
    TradeActionResult,
    TradeSnapshot,
    WhitelistAddIn,
)
from app.services.audit import AuditActor
from app.services.audit import record as audit_record
from app.services.gateway_client import GatewayClient, GatewayError
from app.services.manual_trade import (
    ManualActor,
    cancel_order,
    close_position,
    place_order,
    place_pending,
    recent_deals,
    snapshot,
)

router = APIRouter()


def _actor(current: dict, request: Request) -> ManualActor:
    ip = request.client.host if request.client else ""
    return ManualActor(
        user_id=uuid.UUID(current["sub"]),
        email=str(current.get("email") or current["sub"]),
        ip=ip or "",
    )


# ── read (literal paths before any uuid-param routes) ──────────

@router.get("/accounts/{account_id}/snapshot", response_model=TradeSnapshot)
async def get_snapshot(
    account_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> TradeSnapshot:
    """账户镜像 + 持仓 + 挂单（一次登录往返）。"""
    return TradeSnapshot(**(await snapshot(db, current, account_id=account_id)))


@router.get("/accounts/{account_id}/deals", response_model=list[dict])
async def get_deals(
    account_id: uuid.UUID,
    current: CurrentUser,
    db: DBSession,
    hours: int = Query(default=24, ge=1, le=720, description="回看时长（小时）"),
) -> list[dict]:
    """近 N 小时成交回报（end 缺省为当前时间）。"""
    end = int(time.time())
    start = end - hours * 3600
    return await recent_deals(db, current, account_id=account_id,
                              start=start, end=end)


# ── trade whitelist (gateway gray-release gate; audited) ───────

@router.get("/whitelist")
async def get_trade_whitelist(current: CurrentUser) -> dict:
    """网关交易白名单状态（{trade_enabled, logins}）。"""
    gw = GatewayClient()
    try:
        return await gw.trade_whitelist()
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await gw.aclose()


@router.post("/whitelist")
async def add_trade_whitelist(
    payload: WhitelistAddIn, current: CurrentUser, request: Request
) -> dict:
    """把 MT5 登录号加入网关交易白名单（立即生效并持久化到网关 .env）。"""
    gw = GatewayClient()
    try:
        out = await gw.trade_whitelist_add(payload.login)
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await gw.aclose()
    await audit_record(
        "trade_whitelist_add",
        actor=AuditActor(actor_type="user", actor_id=uuid.UUID(current["sub"]),
                         actor_label=str(current.get("email") or current["sub"])[:120],
                         source_ip=request.client.host if request.client else ""),
        target_type="mt5_account", target_id=str(payload.login),
        params={"logins": out.get("logins", [])},
    )
    return out


@router.delete("/whitelist/{login}")
async def remove_trade_whitelist(
    login: int, current: CurrentUser, request: Request
) -> dict:
    """把 MT5 登录号移出网关交易白名单（立即生效并持久化）。"""
    gw = GatewayClient()
    try:
        out = await gw.trade_whitelist_remove(login)
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await gw.aclose()
    await audit_record(
        "trade_whitelist_remove",
        actor=AuditActor(actor_type="user", actor_id=uuid.UUID(current["sub"]),
                         actor_label=str(current.get("email") or current["sub"])[:120],
                         source_ip=request.client.host if request.client else ""),
        target_type="mt5_account", target_id=str(login),
        params={"logins": out.get("logins", [])},
    )
    return out


# ── write (risk-gated + audited) ───────────────────────────────

@router.post("/order", response_model=TradeActionResult)
async def manual_order(
    payload: ManualOrderRequest, current: CurrentUser, db: DBSession,
    request: Request,
) -> TradeActionResult:
    """市价买入/卖出（风控闸 + 审计）。"""
    out = await place_order(
        db, current, _actor(current, request),
        account_id=payload.account_id, symbol=payload.symbol,
        side=payload.side, volume=payload.volume,
        deviation=payload.deviation, comment=payload.comment,
    )
    return TradeActionResult(**out)


@router.post("/pending", response_model=TradeActionResult)
async def manual_pending(
    payload: ManualPendingRequest, current: CurrentUser, db: DBSession,
    request: Request,
) -> TradeActionResult:
    """挂限价/突破单（风控闸 + 审计）。"""
    out = await place_pending(
        db, current, _actor(current, request),
        account_id=payload.account_id, symbol=payload.symbol,
        order_type=payload.order_type, volume=payload.volume,
        price=payload.price, sl=payload.sl, tp=payload.tp,
        comment=payload.comment,
    )
    return TradeActionResult(**out)


@router.post("/close", response_model=TradeActionResult)
async def manual_close(
    payload: ManualCloseRequest, current: CurrentUser, db: DBSession,
    request: Request,
) -> TradeActionResult:
    """平仓（全平或部分平；减仓只过冻结闸）。"""
    out = await close_position(
        db, current, _actor(current, request),
        account_id=payload.account_id, ticket=payload.ticket,
        volume=payload.volume,
    )
    return TradeActionResult(**out)


@router.post("/cancel", response_model=TradeActionResult)
async def manual_cancel(
    payload: ManualCancelRequest, current: CurrentUser, db: DBSession,
    request: Request,
) -> TradeActionResult:
    """撤销挂单。"""
    out = await cancel_order(
        db, current, _actor(current, request),
        account_id=payload.account_id, ticket=payload.ticket,
    )
    return TradeActionResult(**out)
