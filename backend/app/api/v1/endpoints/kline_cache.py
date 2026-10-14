"""K-line cache endpoints — shared global bar cache, isolated by account type.

浏览器"本地K线数据"的服务端存储：不区分用户，但按 **账户类型（demo/real）隔离**
—— 虚拟下单与实盘的行情品种不同（如实盘经纪商品种带 .E 后缀），各自独立一份。
account_type 由前端按当前活动 MT5 会话传入（默认 demo）。
- GET    /kline-cache                  条目列表（元数据，当前盘）
- GET    /kline-cache/{symbol}/{tf}    该条全部K线
- PUT    /kline-cache/{symbol}/{tf}    覆盖保存（截断到 20000 根；每盘 50 条上限）
- DELETE /kline-cache/{symbol}/{tf}    删除单条
- DELETE /kline-cache                  清空当前盘
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from app.dependencies import CurrentUser, DBSession
from app.models.kline_cache import KlineCache

router = APIRouter()

MAX_BARS_PER_ENTRY = 20000
MAX_ENTRIES = 50


def _norm_account_type(v: str) -> str:
    t = (v or "").strip().lower()
    if t not in ("demo", "real"):
        raise HTTPException(status_code=422, detail="account_type 仅支持 demo / real")
    return t


class KlineCacheSaveIn(PydanticBaseModel):
    bars: list[dict] = Field(
        ..., description="Ascending bars {time,open,high,low,close,tick_volume,spread}"
    )


def _entry_meta(row: KlineCache) -> dict:
    return {
        "symbol": row.symbol,
        "timeframe": row.timeframe,
        "account_type": row.account_type,
        "bars": row.bar_count,
        "firstTs": (row.first_time or 0) * 1000,
        "lastTs": (row.last_time or 0) * 1000,
        "kb": round(row.size_bytes / 1024, 1),
    }


@router.get("")
async def list_cache(
    current: CurrentUser,
    db: DBSession,
    account_type: str = Query(default="demo", description="demo|real — 虚拟下单/实盘缓存隔离"),
) -> list[dict]:
    """列出当前盘的全部缓存条目（按最后覆盖时间倒序）。"""
    t = _norm_account_type(account_type)
    rows = (
        await db.execute(select(KlineCache).where(KlineCache.account_type == t))
    ).scalars().all()
    return sorted((_entry_meta(r) for r in rows), key=lambda m: m["lastTs"], reverse=True)


@router.get("/{symbol}/{timeframe}")
async def get_bars(
    symbol: str,
    timeframe: str,
    current: CurrentUser,
    db: DBSession,
    account_type: str = Query(default="demo"),
) -> dict:
    """该条缓存的全部K线（时间升序）。"""
    t = _norm_account_type(account_type)
    row = (
        await db.execute(
            select(KlineCache).where(
                KlineCache.symbol == symbol.upper(),
                KlineCache.timeframe == timeframe,
                KlineCache.account_type == t,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"本地缓存不存在：{symbol} {timeframe}")
    return {"symbol": row.symbol, "timeframe": row.timeframe, "bars": row.bars}


@router.put("/{symbol}/{timeframe}")
async def save_bars(
    symbol: str,
    timeframe: str,
    body: KlineCacheSaveIn,
    current: CurrentUser,
    db: DBSession,
    account_type: str = Query(default="demo"),
) -> dict:
    """覆盖保存（服务端为真源；截断到最近 20000 根；写入当前盘）。"""
    t = _norm_account_type(account_type)
    bars = body.bars
    if not bars:
        raise HTTPException(status_code=422, detail="bars 不能为空")
    seen: set[int] = set()
    clean: list[dict] = []
    for b in sorted(bars, key=lambda x: int(x.get("time", 0))):
        time_ = int(b.get("time", 0))
        if time_ <= 0 or time_ in seen:
            continue
        seen.add(time_)
        clean.append(
            {
                "time": time_,
                "open": b.get("open"),
                "high": b.get("high"),
                "low": b.get("low"),
                "close": b.get("close"),
                "tick_volume": b.get("tick_volume", 0),
                "spread": b.get("spread", 0),
            }
        )
    clean = clean[-MAX_BARS_PER_ENTRY:]
    payload = json.dumps(clean, separators=(",", ":"))

    row = (
        await db.execute(
            select(KlineCache).where(
                KlineCache.symbol == symbol.upper(),
                KlineCache.timeframe == timeframe,
                KlineCache.account_type == t,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        count = len(
            (await db.execute(select(KlineCache.id).where(KlineCache.account_type == t))).all()
        )
        if count >= MAX_ENTRIES:
            raise HTTPException(
                status_code=409, detail=f"当前盘缓存条目已达上限 {MAX_ENTRIES} 条，请先删除部分条目"
            )
        row = KlineCache(
            symbol=symbol.upper(),
            timeframe=timeframe,
            account_type=t,
            bars=clean,
            bar_count=len(clean),
            first_time=clean[0]["time"],
            last_time=clean[-1]["time"],
            size_bytes=len(payload.encode("utf-8")),
        )
        db.add(row)
    else:
        row.bars = clean
        row.bar_count = len(clean)
        row.first_time = clean[0]["time"]
        row.last_time = clean[-1]["time"]
        row.size_bytes = len(payload.encode("utf-8"))
    await db.commit()
    return _entry_meta(row)


@router.delete("/{symbol}/{timeframe}")
async def delete_entry(
    symbol: str,
    timeframe: str,
    current: CurrentUser,
    db: DBSession,
    account_type: str = Query(default="demo"),
) -> dict:
    """删除单条缓存（当前盘）。"""
    t = _norm_account_type(account_type)
    res = await db.execute(
        sa_delete(KlineCache).where(
            KlineCache.symbol == symbol.upper(),
            KlineCache.timeframe == timeframe,
            KlineCache.account_type == t,
        )
    )
    await db.commit()
    return {"deleted": res.rowcount or 0}


@router.delete("")
async def clear_all(
    current: CurrentUser,
    db: DBSession,
    account_type: str = Query(default="demo"),
) -> dict:
    """清空当前盘的全部缓存。"""
    t = _norm_account_type(account_type)
    res = await db.execute(sa_delete(KlineCache).where(KlineCache.account_type == t))
    await db.commit()
    return {"deleted": res.rowcount or 0}
