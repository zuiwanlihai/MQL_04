"""GET /symbols · /symbols/{name}/rates · /quotes — market data from MT5."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.mt5_client import TF_MAP, client
from app.security import require_gateway_key

router = APIRouter(dependencies=[Depends(require_gateway_key)])


class HistoryProbeRequest(BaseModel):
    timeframe: str


class HistoryBackfillRequest(BaseModel):
    timeframe: str
    start: int = Field(ge=0, description="起始时间（epoch 秒，含）")
    end: int = Field(ge=0, description="结束时间（epoch 秒，不含）")
    max_attempts: int | None = Field(default=None, ge=1, le=20)

# 60s 模块级缓存（symbols 全量拉取较重）
_SYMBOLS_TTL = 60.0
_symbols_cache: tuple[float, list[dict]] | None = None


@router.get("/symbols")
async def symbols() -> list[dict]:
    """Full symbol list (60s cache)."""
    global _symbols_cache
    if _symbols_cache is not None and time.time() - _symbols_cache[0] < _SYMBOLS_TTL:
        return _symbols_cache[1]
    rows = await client.symbols()
    _symbols_cache = (time.time(), rows)
    return rows


@router.get("/symbols/{name}/rates")
async def rates(
    name: str,
    timeframe: Annotated[str, Query(description="M1/M5/M15/M30/H1/H4/D1")],
    limit: Annotated[int, Query(ge=1, le=5000)] = 200,
) -> list[dict]:
    """Candles (epoch seconds, ascending) — 404 for unknown symbols."""
    tf = timeframe.upper()
    if tf not in TF_MAP:
        raise HTTPException(
            status_code=422,
            detail=f"不支持的周期：{timeframe}，可选 {'/'.join(TF_MAP)}",
        )
    return await client.rates(name, tf, limit)


@router.get("/symbols/{name}/rates/range")
async def rates_range(
    name: str,
    timeframe: Annotated[str, Query(description="M1/M5/M15/M30/H1/H4/D1")],
    start: Annotated[int, Query(ge=0, description="起始时间（epoch 秒，含）")],
    end: Annotated[int, Query(ge=0, description="结束时间（epoch 秒，不含）")],
    limit: Annotated[int, Query(ge=1, le=5000)] = 5000,
) -> list[dict]:
    """Candles in [start, end) ascending (epoch seconds) — feeds backtests.

    404 for unknown symbols, 422 for invalid range/timeframe.
    """
    tf = timeframe.upper()
    if tf not in TF_MAP:
        raise HTTPException(
            status_code=422,
            detail=f"不支持的周期：{timeframe}，可选 {'/'.join(TF_MAP)}",
        )
    if start >= end:
        raise HTTPException(status_code=422, detail="起始时间必须早于结束时间")
    date_from = datetime.fromtimestamp(start, UTC)
    date_to = datetime.fromtimestamp(end, UTC)
    return await client.rates_range(name, tf, date_from, date_to, limit)


@router.get("/symbols/{name}/ticks/range")
async def ticks_range(
    name: str,
    start: Annotated[int, Query(ge=0, description="起始时间（epoch 秒，含）")],
    end: Annotated[int, Query(ge=0, description="结束时间（epoch 秒，不含）")],
    limit: Annotated[int, Query(ge=1, le=200_000)] = 100_000,
) -> list[dict]:
    """Ticks in [start, end) ascending (time_msc ms) — 404 unknown symbol.

    Response rows are capped at ``limit``; callers slicing wide windows should
    poll with narrower sub-ranges (the backend tick sync chunks by 6h).
    """
    if start >= end:
        raise HTTPException(status_code=422, detail="起始时间必须早于结束时间")
    date_from = datetime.fromtimestamp(start, UTC)
    date_to = datetime.fromtimestamp(end, UTC)
    return await client.ticks_range(name, date_from, date_to, limit)


@router.post("/symbols/{name}/history/probe")
async def history_probe(name: str, body: HistoryProbeRequest) -> dict:
    """Probe the terminal-local cached bar range for a symbol/timeframe."""
    tf = body.timeframe.upper()
    if tf not in TF_MAP:
        raise HTTPException(
            status_code=422,
            detail=f"不支持的周期：{body.timeframe}，可选 {'/'.join(TF_MAP)}",
        )
    return await client.history_probe(name, tf)


@router.post("/symbols/{name}/history/backfill")
async def history_backfill(name: str, body: HistoryBackfillRequest) -> dict:
    """Retry a [start, end) window until the terminal finishes downloading."""
    tf = body.timeframe.upper()
    if tf not in TF_MAP:
        raise HTTPException(
            status_code=422,
            detail=f"不支持的周期：{body.timeframe}，可选 {'/'.join(TF_MAP)}",
        )
    if body.start >= body.end:
        raise HTTPException(status_code=422, detail="起始时间必须早于结束时间")
    return await client.history_backfill(
        name, tf, body.start, body.end, body.max_attempts
    )


@router.get("/quotes")
async def quotes(
    symbols: Annotated[str, Query(description="逗号分隔品种名")],
) -> list[dict]:
    """Batch tick quotes; unknown/unpriced symbols are skipped."""
    names = [n.strip() for n in symbols.split(",") if n.strip()]
    if not names:
        return []
    return await client.quotes(names[:100])
