"""Market history management service (M2 数据中心).

「已缓存数据范围」= MT5 终端本地可用的 K 线范围。K 线本身不落库
（网关为唯一权威来源），这里只维护 ``market_data_ranges`` 元数据：

- :func:`probe_and_upsert` — 探测终端本地范围并 upsert 快照（探测即追踪）
- :func:`backfill` — 按 WINDOW_BARS 切时间窗串行调网关补数（终端会异步向
  券商下载），完成后 probe 刷新快照；全局 Semaphore 串行化避免并发占用网关
- :func:`sync_round` — 后台轮转刷新 last_bar_time（每对只调
  ``client.rates(sym, tf, 2)``，first/bars_count 是 probe 快照不动）
- :func:`run_sync_loop` — lifespan 常驻循环（照抄 heartbeat_loop 骨架）
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from math import ceil

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.engine.session import AsyncSessionLocal
from app.models import MarketDataRange
from app.services.gateway_client import (
    GatewayClient,
    GatewayError,
    GatewayUnavailable,
)

logger = logging.getLogger("mt5quant.market_history")

TF_SECONDS: dict[str, int] = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400,
}

# 补数全局串行化：同一时间只允许一个补数流程占用网关（重IO操作）
_backfill_semaphore = asyncio.Semaphore(1)

# 每 (symbol, timeframe) 一把锁：防止同一对的 probe/backfill/删除交错
_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _lock(symbol: str, timeframe: str) -> asyncio.Lock:
    key = (symbol, timeframe)
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


def _to_dt(epoch: int | None) -> datetime | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(int(epoch), tz=UTC)


def _new_client() -> GatewayClient:
    return GatewayClient()


async def get_range(
    db: AsyncSession, symbol: str, timeframe: str
) -> MarketDataRange | None:
    return (await db.execute(
        select(MarketDataRange).where(
            MarketDataRange.symbol == symbol,
            MarketDataRange.timeframe == timeframe,
        )
    )).scalar_one_or_none()


async def coverage(db: AsyncSession) -> list[MarketDataRange]:
    """All tracked pairs ordered by symbol/timeframe."""
    return list((await db.execute(
        select(MarketDataRange)
        .order_by(MarketDataRange.symbol.asc(), MarketDataRange.timeframe.asc())
    )).scalars().all())


async def probe_and_upsert(
    db: AsyncSession,
    symbol: str,
    timeframe: str,
    client: GatewayClient | None = None,
) -> MarketDataRange:
    """Probe the terminal-local range and upsert the tracking snapshot."""
    own = client is None
    if own:
        client = _new_client()
    assert client is not None, "client must be set after _new_client or caller-provided"
    try:
        data = await client.probe_history(
            symbol, timeframe, timeout=settings.MARKET_HISTORY_PROBE_TIMEOUT
        )
    finally:
        if own:
            await client.aclose()

    row = await get_range(db, symbol, timeframe)
    if row is None:
        row = MarketDataRange(symbol=symbol, timeframe=timeframe)
        db.add(row)
    row.first_bar_time = _to_dt(data.get("first_bar_time"))
    row.last_bar_time = _to_dt(data.get("last_bar_time"))
    row.bars_count = int(data.get("bars_count") or 0)
    row.probe_truncated = bool(data.get("truncated"))
    row.last_error = None
    row.last_synced_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return row


async def backfill(
    db: AsyncSession,
    symbol: str,
    timeframe: str,
    start: int,
    end: int,
) -> dict:
    """Window-sliced serial backfill; finishes with a probe refresh.

    跨度以折算根数校验（≤ BACKFILL_MAX_BARS），超出抛 ``ValueError``（端点
    映射 422）。补数幂等崩溃安全：已下载的数据留在终端本地，中断重试无害。
    """
    tf_s = TF_SECONDS[timeframe]
    span_bars = ceil((end - start) / tf_s)
    max_bars = settings.MARKET_HISTORY_BACKFILL_MAX_BARS
    if span_bars > max_bars:
        raise ValueError(
            f"单次补数跨度不能超过 {max_bars} 根 K 线（当前约 {span_bars} 根），"
            "请缩小时间范围"
        )

    window_bars = settings.MARKET_HISTORY_BACKFILL_WINDOW_BARS
    windows = 0
    attempts = 0
    complete = True
    async with _backfill_semaphore, _lock(symbol, timeframe):
        client = _new_client()
        try:
            cur = start
            while cur < end:
                nxt = min(end, cur + window_bars * tf_s)
                windows += 1
                out = await client.history_backfill(
                    symbol, timeframe, cur, nxt,
                    max_attempts=settings.MARKET_HISTORY_BACKFILL_MAX_ATTEMPTS,
                    timeout=settings.MARKET_HISTORY_BACKFILL_TIMEOUT,
                )
                attempts += int(out.get("attempts") or 0)
                if not out.get("complete"):
                    complete = False
                cur = nxt
        finally:
            await client.aclose()

        row: MarketDataRange | None
        try:
            row = await probe_and_upsert(db, symbol, timeframe)
        except GatewayError as exc:
            # probe 失败不吞补数结果——用回补前的快照返回
            logger.warning("backfill finishing probe failed: %s", exc.detail)
            row = await get_range(db, symbol, timeframe)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "first_bar_time": row.first_bar_time if row else None,
        "last_bar_time": row.last_bar_time if row else None,
        "bars_count": row.bars_count if row else 0,
        "windows": windows,
        "attempts": attempts,
        "complete": complete,
        "requested_start": start,
        "requested_end": end,
    }


async def sync_round(db: AsyncSession, max_pairs: int | None = None) -> dict:
    """Rotate one batch of tracked pairs and refresh ``last_bar_time``.

    - GatewayUnavailable → 置 last_error 并立即结束本轮（网关挂了，重试无意义）
    - 单对其它 GatewayError → 记 last_error 继续
    - 已请求的对（含失败对）都推进 last_synced_at（轮转游标）
    """
    if max_pairs is None:
        max_pairs = settings.MARKET_HISTORY_SYNC_BATCH
    rows = list((await db.execute(
        select(MarketDataRange)
        .order_by(MarketDataRange.last_synced_at.asc().nulls_first())
        .limit(max_pairs)
    )).scalars().all())
    if not rows:
        return {"updated": 0, "failed": 0}

    updated = 0
    failed = 0
    client = _new_client()
    try:
        for row in rows:
            try:
                rates = await client.rates(row.symbol, row.timeframe, 2)
            except GatewayUnavailable:
                row.last_error = "网关不可达"
                row.last_synced_at = datetime.now(UTC)
                failed += 1
                break
            except GatewayError as exc:
                row.last_error = exc.detail
                row.last_synced_at = datetime.now(UTC)
                failed += 1
                continue
            row.last_error = None
            row.last_synced_at = datetime.now(UTC)
            if rates:
                row.last_bar_time = datetime.fromtimestamp(
                    int(rates[-1]["time"]), tz=UTC
                )
            updated += 1
            if settings.MARKET_HISTORY_SYNC_PAIR_DELAY > 0:
                await asyncio.sleep(settings.MARKET_HISTORY_SYNC_PAIR_DELAY)
    finally:
        await client.aclose()
    await db.commit()
    return {"updated": updated, "failed": failed}


async def remove_tracking(db: AsyncSession, symbol: str, timeframe: str) -> bool:
    row = await get_range(db, symbol, timeframe)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


async def run_sync_loop() -> None:
    """Forever loop — started from the FastAPI lifespan when enabled."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                res = await sync_round(db)
                if res["failed"]:
                    logger.warning(
                        "market history sync round: updated=%s failed=%s",
                        res["updated"], res["failed"],
                    )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never let the loop die
            logger.exception("market history sync loop failed")
        await asyncio.sleep(settings.MARKET_HISTORY_SYNC_INTERVAL_SECONDS)
