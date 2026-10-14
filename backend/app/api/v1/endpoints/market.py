"""
Market endpoints: symbol list/detail, klines and quotes.

本期不落库行情 — everything is proxied from the MT5 gateway and mapped
onto the frontend contract types.  Gateway failures surface as 503/404
with Chinese details.
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import CurrentUser
from app.schemas.market import (
    DataQualityReport,
    MarketTimeframe,
    PriceBarOut,
    QuoteListResponse,
    SymbolListResponse,
    SymbolOut,
)
from app.services.gateway_client import (
    GatewayClient,
    GatewayError,
    GatewaySymbolNotFound,
    get_gateway_client,
)
from app.services.market_mapper import map_bar, map_quote, map_symbol
from app.services.market_quality import analyze_quality
from app.services.quote_push import TICKET_TTL_SECONDS, get_quote_hub

router = APIRouter()

GatewayDep = Annotated[GatewayClient, Depends(get_gateway_client)]


def _fail(exc: GatewayError) -> None:
    """Translate gateway errors into HTTP errors (never returns)."""
    if isinstance(exc, GatewaySymbolNotFound):
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    raise HTTPException(status_code=503, detail=exc.detail) from exc


@router.get("/symbols", response_model=SymbolListResponse)
async def list_symbols(
    current: CurrentUser,
    gateway: GatewayDep,
    group: str | None = Query(default=None, description="分组过滤：forex/indexes/…"),
    q: str | None = Query(default=None, description="名称/描述模糊搜索"),
) -> SymbolListResponse:
    """Full symbol list from the gateway, filtered in memory."""
    try:
        raw = await gateway.symbols()
    except GatewayError as exc:
        _fail(exc)

    items = [map_symbol(row) for row in raw]
    if group:
        items = [s for s in items if s.group == group]
    if q:
        needle = q.strip().lower()
        items = [
            s for s in items if needle in s.name.lower() or needle in s.description.lower()
        ]
    return SymbolListResponse(items=items, total=len(items))


@router.get("/symbols/{name}", response_model=SymbolOut)
async def get_symbol(
    name: str,
    current: CurrentUser,
    gateway: GatewayDep,
) -> SymbolOut:
    """Single symbol detail; 404 when the gateway does not know it."""
    try:
        raw = await gateway.symbols()
    except GatewayError as exc:
        _fail(exc)

    needle = name.lower()
    for row in raw:
        if str(row.get("name") or row.get("symbol") or "").lower() == needle:
            return map_symbol(row)
    raise HTTPException(status_code=404, detail=f"品种不存在：{name}")


@router.get("/symbols/{name}/klines", response_model=list[PriceBarOut])
async def get_klines(
    name: str,
    current: CurrentUser,
    gateway: GatewayDep,
    timeframe: MarketTimeframe = Query(default="D1"),
    limit: int = Query(default=500, ge=1, le=5000),
    start: int | None = Query(
        default=None, ge=0, description="开始时间（epoch 秒，含），与 end 搭配用于分段取历史"
    ),
    end: int | None = Query(default=None, ge=0, description="结束时间（epoch 秒，不含），默认最新"),
) -> list[PriceBarOut]:
    """Candles ascending by time (epoch seconds — feeds lightweight-charts).

    传 start/end 时走网关时间窗接口（分段下载历史用），否则取最近 limit 根。
    """
    try:
        if start is not None or end is not None:
            e = int(end if end is not None else time.time())
            s = int(start if start is not None else e - 7 * 86400)
            raw = await gateway.rates_range(name.upper(), timeframe, s, e, limit)
        else:
            raw = await gateway.rates(name.upper(), timeframe, limit)
    except GatewayError as exc:
        _fail(exc)
    bars = [map_bar(row) for row in raw]
    bars.sort(key=lambda b: b.time)
    return bars


@router.get("/quotes", response_model=QuoteListResponse)
async def list_quotes(
    current: CurrentUser,
    gateway: GatewayDep,
    symbols: str = Query(..., min_length=1, max_length=1024, description="逗号分隔品种名"),
) -> QuoteListResponse:
    """Batch quotes (1–50 symbols) with derived change/spread/day range."""
    names = [n.strip().upper() for n in symbols.split(",") if n.strip()]
    if not 1 <= len(names) <= 50:
        raise HTTPException(
            status_code=422, detail="symbols 数量须在 1–50 之间"
        )
    try:
        raw = await gateway.quotes(names)
    except GatewayError as exc:
        _fail(exc)
    items = [map_quote(row) for row in raw]
    return QuoteListResponse(items=items, total=len(items))


@router.post("/ws-ticket")
async def create_ws_ticket(current: CurrentUser) -> dict:
    """Issue a one-time short-TTL ticket for the WS quote stream.

    Browsers cannot set an Authorization header on the WebSocket handshake;
    the ticket keeps the JWT out of URLs / access logs.
    """
    hub = get_quote_hub()
    if hub is None:
        raise HTTPException(status_code=503, detail="行情推送未启用")
    return {"ticket": hub.issue_ticket(), "expires_in": TICKET_TTL_SECONDS}


@router.get("/quality", response_model=DataQualityReport)
async def data_quality(
    current: CurrentUser,
    gateway: GatewayDep,
    symbol: str = Query(..., min_length=1, max_length=32, description="品种名，如 EURUSD"),
    timeframe: MarketTimeframe = Query(default="H1"),
    start: int | None = Query(default=None, ge=0, description="开始时间（epoch 秒），默认 7 天前"),
    end: int | None = Query(default=None, ge=0, description="结束时间（epoch 秒），默认当前"),
) -> DataQualityReport:
    """Bar quality check (数据中心 P1)：缺口 / 异常值 / 重复，即时分析不落库。"""
    end_ts = end or int(time.time())
    start_ts = start or (end_ts - 7 * 86400)
    if start_ts >= end_ts:
        raise HTTPException(status_code=422, detail="start 必须早于 end")
    try:
        report = await analyze_quality(
            gateway, symbol.strip().upper(), timeframe, start_ts, end_ts
        )
    except GatewayError as exc:
        _fail(exc)
    return DataQualityReport(**report)
