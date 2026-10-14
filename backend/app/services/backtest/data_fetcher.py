"""Bar-window fetching for backtests (M4 Phase D).

Pages the gateway's ``rates_range`` endpoint with a 5000-bar window that
rolls forward by ``5000 × tf_seconds`` per page, de-duplicates by bar time
and sorts ascending.  The warm-up bars are fetched *before* the trading
window so the strategy's indicators have history at bar 0 (PRD M4: 预热期
on_bar 照常运行，下单拒 WARMUP_PERIOD).

``estimate_bars`` is the pre-enqueue guard: the API rejects obviously
oversized windows (``BACKTEST_MAX_BARS``) with 422 without contacting the
gateway; the exact count is verified again after the fetch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from app.config import settings
from app.sandbox.matching import TF_SECONDS
from app.sandbox.payload import BarArrays
from app.services.gateway_client import GatewayClient

logger = logging.getLogger("mt5quant.backtest.fetch")

PAGE_BARS = 5000  # gateway rates_range limit ceiling


class DataUnavailableError(Exception):
    """Raised when the window contains no bars at all (user-facing Chinese)."""


@dataclass
class FetchedBars:
    """Bars covering [start − warmup·tf, end) + the actual warm-up count."""

    arrays: BarArrays
    warmup_bars: int

    @property
    def n_bars(self) -> int:
        return len(self.arrays)

    @property
    def fingerprint(self) -> dict:
        """Data fingerprint frozen into config_snapshot (reproducibility)."""
        t = self.arrays.time
        return {
            "first_ts": int(t[0]) if len(t) else None,
            "last_ts": int(t[-1]) if len(t) else None,
            "n_bars": len(t),
        }


def estimate_bars(
    start: datetime, end: datetime, timeframe: str, warmup_bars: int
) -> int:
    """Upper-bound bar count for a window (weekends included — conservative)."""
    tf = TF_SECONDS[timeframe]
    seconds = max((end - start).total_seconds(), 0.0)
    return int(seconds // tf) + warmup_bars + 1


def _to_epoch(dt: datetime) -> int:
    if dt.tzinfo is None:  # treat naive input as UTC
        return int(dt.replace(tzinfo=UTC).timestamp())
    return int(dt.timestamp())


async def fetch_bars(
    client: GatewayClient,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    warmup_bars: int,
) -> FetchedBars:
    """Fetch warm-up + trading-window bars via paginated rates_range."""
    tf = TF_SECONDS[timeframe]
    start_epoch = _to_epoch(start)
    end_epoch = _to_epoch(end)
    fetch_start = start_epoch - warmup_bars * tf
    page_span = PAGE_BARS * tf

    by_time: dict[int, dict] = {}
    cursor = fetch_start
    max_pages = (end_epoch - fetch_start) // page_span + 2
    for _ in range(max_pages):
        if cursor >= end_epoch:
            break
        page_end = min(cursor + page_span, end_epoch)
        rows = await client.rates_range(symbol, timeframe, cursor, page_end)
        if not rows:
            cursor = page_end
            continue
        for r in rows:
            t = int(r["time"])
            if fetch_start <= t < end_epoch:
                by_time[t] = r
        last = int(rows[-1]["time"])
        if last < cursor:  # gateway returned nothing ahead of the cursor
            cursor = page_end
        else:
            cursor = max(page_end, last + 1)

    if not by_time:
        raise DataUnavailableError(
            f"区间内无 K 线数据：{symbol} {timeframe}，请检查时间范围或网关历史数据"
        )

    times = sorted(by_time)
    n = len(times)
    arrays = BarArrays(
        time=np.asarray(times, dtype="int64"),
        open=np.asarray([by_time[t]["open"] for t in times], dtype="float64"),
        high=np.asarray([by_time[t]["high"] for t in times], dtype="float64"),
        low=np.asarray([by_time[t]["low"] for t in times], dtype="float64"),
        close=np.asarray([by_time[t]["close"] for t in times], dtype="float64"),
        tick_volume=np.asarray(
            [by_time[t].get("tick_volume", 0) for t in times], dtype="int64"
        ),
        spread=np.asarray([by_time[t].get("spread", 0) for t in times], dtype="int64"),
    )

    # Bars strictly before the trading window are the warm-up region.
    warmup_actual = int(np.searchsorted(arrays.time, start_epoch, side="left"))
    if n > settings.BACKTEST_MAX_BARS:
        raise DataUnavailableError(
            f"实际取数 {n} 根超过上限 {settings.BACKTEST_MAX_BARS}，请缩小区间或提高周期"
        )
    logger.info(
        "fetched %s %s: %d bars (warmup=%d, window=[%d,%d))",
        symbol, timeframe, n, warmup_actual, start_epoch, end_epoch,
    )
    return FetchedBars(arrays=arrays, warmup_bars=warmup_actual)


def bars_from_local(
    rows: list[dict],
    start: datetime,
    end: datetime,
    timeframe: str,
    warmup_bars: int,
) -> FetchedBars:
    """本地缓存 K 线 → FetchedBars（data_source=local 时替代网关取数）。

    rows: [{"time": epoch秒, "open","high","low","close", "tick_volume"?, "spread"?}]
    处理逻辑与 fetch_bars 对齐：排序去重 → 窗口过滤 [start−warmup·tf, end) →
    warmup_actual 按 start_epoch 二分定位。数据不足时抛 DataUnavailableError。
    """
    tf = TF_SECONDS[timeframe]
    start_epoch = _to_epoch(start)
    end_epoch = _to_epoch(end)
    fetch_start = start_epoch - warmup_bars * tf

    seen: set[int] = set()
    by_time: dict[int, dict] = {}
    for r in rows:
        t = int(r.get("time", 0))
        o, h, lo, c = (float(r.get(k, 0) or 0) for k in ("open", "high", "low", "close"))
        if (
            t <= 0 or t in seen
            or not all(x == x and abs(x) < 1e12 for x in (o, h, lo, c))  # NaN/inf
            or h < lo or h < max(o, c) - 1e-9 or lo > min(o, c) + 1e-9
        ):
            continue  # 脏行丢弃
        seen.add(t)
        if not (fetch_start <= t < end_epoch):
            continue
        by_time[t] = {
            "open": o, "high": h, "low": lo, "close": c,
            "tick_volume": int(r.get("tick_volume", 0) or 0),
            "spread": int(r.get("spread", 0) or 0),
        }
    times = sorted(by_time)
    n = len(times)
    if n == 0:
        raise DataUnavailableError(
            "本地缓存数据与回测区间无交集：请调整区间，或先在行情页浏览该品种积累缓存"
        )
    if n < 30:
        raise DataUnavailableError(f"本地缓存区间内仅 {n} 根K线，不足以回测（至少30根）")
    arrays = BarArrays(
        time=np.asarray(times, dtype="int64"),
        open=np.asarray([by_time[t]["open"] for t in times], dtype="float64"),
        high=np.asarray([by_time[t]["high"] for t in times], dtype="float64"),
        low=np.asarray([by_time[t]["low"] for t in times], dtype="float64"),
        close=np.asarray([by_time[t]["close"] for t in times], dtype="float64"),
        tick_volume=np.asarray([by_time[t]["tick_volume"] for t in times], dtype="int64"),
        spread=np.asarray([by_time[t]["spread"] for t in times], dtype="int64"),
    )
    warmup_actual = int(np.searchsorted(arrays.time, start_epoch, side="left"))
    logger.info(
        "local bars %s %s: %d rows in, %d bars kept (warmup=%d)",
        timeframe, len(rows), n, warmup_actual,
    )
    return FetchedBars(arrays=arrays, warmup_bars=warmup_actual)
