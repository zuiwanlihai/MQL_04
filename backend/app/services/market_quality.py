"""Data quality analysis (数据中心 P1: 缺口/异常值).

Compute-on-read: bars are fetched from the MT5 gateway (the single source
of truth per PRD M2) in pages and analyzed in memory — nothing is stored.

Checks:
- gap            consecutive bars farther apart than 1.5× the timeframe.
                 Gaps that start on Thursday/Friday and span the weekend
                 are classified ``weekend`` (info, normal FX schedule);
                 other gaps are errors.
- invalid_ohlc   high < max(open, close), low > min(open, close),
                 high < low, or non-positive prices (error).
- price_spike    |close − prev_close| / prev_close > 20% (warning — may be
                 a genuine news move, e.g. crypto).
- duplicate_time repeated bar timestamps (error).
- zero_volume    tick_volume == 0 (info).

Score: 100 − 2 per non-weekend gap (cap 40) − 3 per invalid bar (cap 30)
− 1 per duplicate (cap 10) − 0.2 per spike (cap 20).  Verdict: ≥90 ok,
≥60 warn, else poor.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from app.services.gateway_client import GatewayClient

TF_SECONDS: dict[str, int] = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400,
}

PAGE_LIMIT = 5000
MAX_PAGES = 12  # ≈60k bars per analysis
MAX_ISSUES_PER_CODE = 50

_SPIKE_RATIO = 0.20


def _issues_for(
    code: str, severity: str, found: list[tuple], fmt: object
) -> list[dict]:
    """Render at most MAX_ISSUES_PER_CODE issues, prefixing a truncation note."""
    out = []
    for idx, item in enumerate(found[:MAX_ISSUES_PER_CODE]):
        out.append(fmt(idx, item))
    if len(found) > MAX_ISSUES_PER_CODE:
        out.append({
            "severity": severity,
            "code": code,
            "message": f"该类问题共 {len(found)} 处，仅显示前 {MAX_ISSUES_PER_CODE} 条",
            "start_time": None,
            "end_time": None,
        })
    return out


async def analyze_quality(
    client: GatewayClient, symbol: str, timeframe: str, start: int, end: int
) -> dict:
    """Fetch bars in [start, end) from the gateway and analyze quality."""
    tf = TF_SECONDS[timeframe]
    rows: list[dict] = []
    cursor = start
    truncated = False
    for _ in range(MAX_PAGES):
        page = await client.rates_range(
            symbol, timeframe, cursor, end, limit=PAGE_LIMIT
        )
        if not page:
            break
        rows.extend(page)
        if len(page) < PAGE_LIMIT:
            break
        last_t = int(page[-1].get("time", 0))
        cursor = last_t + tf
        if cursor >= end:
            break
    else:
        truncated = True

    # De-duplicate by time (gateway overlap guard), keep first occurrence.
    seen: set[int] = set()
    bars: list[dict] = []
    dup_times: list[int] = []
    for r in rows:
        t = int(r.get("time", 0))
        if t in seen:
            dup_times.append(t)
            continue
        seen.add(t)
        bars.append(r)
    bars.sort(key=lambda r: int(r["time"]))

    issues: list[dict] = []
    gap_count = 0
    weekend_gap_count = 0
    invalid_count = 0
    spike_count = 0
    zero_volume_count = 0

    gaps: list[tuple[int, int, int]] = []  # (from, to, missing)
    weekend_gaps: list[tuple[int, int, int]] = []
    invalids: list[tuple[int, str]] = []  # (time, reason)
    spikes: list[tuple[int, float]] = []  # (time, ratio)
    zero_vols: list[int] = []

    prev_close: float | None = None
    prev_t: int | None = None
    for b in bars:
        t = int(b["time"])
        o = float(b.get("open", 0) or 0)
        h = float(b.get("high", 0) or 0)
        low = float(b.get("low", 0) or 0)
        c = float(b.get("close", 0) or 0)
        vol = int(b.get("tick_volume", 0) or 0)

        if prev_t is not None and t - prev_t > tf * 1.5:
            missing = max(0, round((t - prev_t) / tf) - 1)
            # A gap starting Thu/Fri and crossing into the weekend is the
            # normal FX market closure — informational, not an error.
            start_wd = datetime.fromtimestamp(prev_t, tz=UTC).weekday()
            crosses_weekend = (t - prev_t) > (5 - start_wd) * 86400 if start_wd >= 3 else False
            if crosses_weekend:
                weekend_gaps.append((prev_t, t, missing))
            else:
                gaps.append((prev_t, t, missing))

        if prev_close is not None and prev_close > 0:
            ratio = abs(c - prev_close) / prev_close
            if ratio > _SPIKE_RATIO:
                spikes.append((t, ratio))

        if o <= 0 or h <= 0 or low <= 0 or c <= 0:
            invalids.append((t, "存在非正价格"))
        elif h < max(o, c) or low > min(o, c) or h < low:
            invalids.append((t, "OHLC 关系非法（high/low 未包络 open/close）"))

        if vol == 0:
            zero_vols.append(t)

        prev_t, prev_close = t, c

    gap_count = len(gaps)
    weekend_gap_count = len(weekend_gaps)
    invalid_count = len(invalids)
    spike_count = len(spikes)
    duplicate_count = len(dup_times)
    zero_volume_count = len(zero_vols)

    def _iso(t: int) -> str:
        return datetime.fromtimestamp(t, tz=UTC).strftime("%m-%d %H:%M")

    issues.extend(_issues_for("gap", "error", gaps, lambda _i, g: {
        "severity": "error",
        "code": "gap",
        "message": f"K 线缺口：{_iso(g[0])} → {_iso(g[1])}（缺失约 {g[2]} 根）",
        "start_time": g[0],
        "end_time": g[1],
    }))
    issues.extend(_issues_for("weekend_gap", "info", weekend_gaps, lambda _i, g: {
        "severity": "info",
        "code": "weekend_gap",
        "message": f"周末休市：{_iso(g[0])} → {_iso(g[1])}（缺失约 {g[2]} 根，属正常休市）",
        "start_time": g[0],
        "end_time": g[1],
    }))
    issues.extend(_issues_for("invalid_ohlc", "error", invalids, lambda _i, v: {
        "severity": "error",
        "code": "invalid_ohlc",
        "message": f"无效 K 线 {_iso(v[0])}：{v[1]}",
        "start_time": v[0],
        "end_time": v[0],
    }))
    issues.extend(_issues_for("price_spike", "warning", spikes, lambda _i, s: {
        "severity": "warning",
        "code": "price_spike",
        "message": f"价格跳变 {_iso(s[0])}：较前根收盘变动 {s[1] * 100:.1f}%",
        "start_time": s[0],
        "end_time": s[0],
    }))
    issues.extend(_issues_for("duplicate_time", "error", dup_times, lambda _i, d: {
        "severity": "error",
        "code": "duplicate_time",
        "message": f"重复时间戳 {_iso(d)}",
        "start_time": d,
        "end_time": d,
    }))
    issues.extend(_issues_for("zero_volume", "info", zero_vols, lambda _i, z: {
        "severity": "info",
        "code": "zero_volume",
        "message": f"零成交 K 线 {_iso(z)}",
        "start_time": z,
        "end_time": z,
    }))

    score = 100.0
    score -= min(40.0, gap_count * 2.0)
    score -= min(30.0, invalid_count * 3.0)
    score -= min(10.0, duplicate_count * 1.0)
    score -= min(20.0, spike_count * 0.2)
    quality_score = max(0, math.ceil(score))
    verdict = "ok" if quality_score >= 90 else ("warn" if quality_score >= 60 else "poor")

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "start": start,
        "end": end,
        "bars_analyzed": len(bars),
        "quality_score": quality_score,
        "verdict": verdict,
        "gap_count": gap_count,
        "weekend_gap_count": weekend_gap_count,
        "invalid_count": invalid_count,
        "spike_count": spike_count,
        "duplicate_count": duplicate_count,
        "zero_volume_count": zero_volume_count,
        "issues": issues,
        "truncated": truncated,
    }
