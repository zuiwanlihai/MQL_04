"""Market history management endpoints (M2 数据中心).

Probe/backfill proxy to the gateway; tracking metadata lives in
``market_data_ranges``.  Literal routes are registered before path-parameter
routes (project convention — GET /coverage before DELETE /coverage/{s}/{t}).
"""

from __future__ import annotations

import uuid as uuid_mod

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.dependencies import CurrentUser, DBSession
from app.schemas.market_history import (
    HistoryBackfillRequest,
    HistoryBackfillResponse,
    HistoryCoverageResponse,
    HistoryProbeRequest,
    HistoryRemoveResponse,
    HistorySyncResponse,
    MarketDataRangeOut,
)
from app.services import audit
from app.services import market_history as mh
from app.services.gateway_client import GatewayError, GatewaySymbolNotFound

router = APIRouter()


def _user_id(current: CurrentUser) -> uuid_mod.UUID:
    return uuid_mod.UUID(str(current["sub"]))


def _actor(current: CurrentUser) -> audit.AuditActor:
    return audit.AuditActor(
        actor_type="user",
        actor_id=_user_id(current),
        actor_label=str(current.get("username") or ""),
    )


def _fail(exc: GatewayError) -> None:
    """Translate gateway errors into HTTP errors (never returns)."""
    if isinstance(exc, GatewaySymbolNotFound):
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    raise HTTPException(status_code=503, detail=exc.detail) from exc


@router.get("/coverage", response_model=HistoryCoverageResponse)
async def get_coverage(
    current: CurrentUser,
    db: DBSession,
) -> HistoryCoverageResponse:
    """All tracked (symbol, timeframe) terminal-local coverage rows."""
    rows = await mh.coverage(db)
    items = [MarketDataRangeOut.model_validate(r) for r in rows]
    return HistoryCoverageResponse(items=items, total=len(items))


@router.post("/probe", response_model=MarketDataRangeOut)
async def probe(
    body: HistoryProbeRequest,
    current: CurrentUser,
    db: DBSession,
) -> MarketDataRangeOut:
    """Probe the terminal-local range and create/update tracking."""
    symbol = body.symbol.strip().upper()
    try:
        row = await mh.probe_and_upsert(db, symbol, body.timeframe)
    except GatewayError as exc:
        _fail(exc)
    return MarketDataRangeOut.model_validate(row)


@router.post("/backfill", response_model=HistoryBackfillResponse)
async def backfill(
    body: HistoryBackfillRequest,
    current: CurrentUser,
    db: DBSession,
) -> HistoryBackfillResponse:
    """Pull [start, end) into the terminal (window-sliced, serialised)."""
    symbol = body.symbol.strip().upper()
    if body.end <= body.start:
        raise HTTPException(status_code=422, detail="结束时间必须晚于起始时间")
    if await mh.get_range(db, symbol, body.timeframe) is None:
        raise HTTPException(
            status_code=404, detail="该品种/周期尚未追踪，请先「探测并追踪」"
        )
    try:
        out = await mh.backfill(db, symbol, body.timeframe, body.start, body.end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GatewayError as exc:
        _fail(exc)
    await audit.record(
        "market_history.backfill",
        actor=_actor(current),
        target_type="symbol",
        target_id=symbol,
        params={
            "timeframe": body.timeframe,
            "start": body.start,
            "end": body.end,
            "complete": out["complete"],
            "attempts": out["attempts"],
        },
        db=db,
    )
    return HistoryBackfillResponse(**out)


@router.post("/sync", response_model=HistorySyncResponse)
async def sync_now(
    current: CurrentUser,
    db: DBSession,
) -> HistorySyncResponse:
    """Run one incremental sync round immediately (rotation by last_synced_at)."""
    if not settings.MARKET_HISTORY_SYNC_ENABLED:
        raise HTTPException(status_code=503, detail="历史数据同步功能未启用")
    res = await mh.sync_round(db)
    return HistorySyncResponse(**res)


@router.delete("/coverage/{symbol}/{timeframe}", response_model=HistoryRemoveResponse)
async def remove_coverage(
    symbol: str,
    timeframe: str,
    current: CurrentUser,
    db: DBSession,
) -> HistoryRemoveResponse:
    """Stop tracking one (symbol, timeframe) pair (audit-logged)."""
    symbol = symbol.strip().upper()
    timeframe = timeframe.upper()
    removed = await mh.remove_tracking(db, symbol, timeframe)
    if not removed:
        raise HTTPException(status_code=404, detail="未找到该追踪记录")
    await audit.record(
        "market_history.untrack",
        actor=_actor(current),
        target_type="symbol",
        target_id=symbol,
        params={"timeframe": timeframe},
        db=db,
    )
    return HistoryRemoveResponse(symbol=symbol, timeframe=timeframe, removed=True)
