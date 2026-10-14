"""Slippage calibration + live execution deviation report (M4 P1).

Both features are **compute-on-read** over ``live_trades`` — no snapshot
table, no watcher: a deal row becomes usable the moment ``_record_deal``
persists it, and stats are always current.

Slippage definition (MT5 deviation semantics, adverse-positive):
    buy : (deal_price − ask@request) / point     positive = paid more
    sell: (bid@request − deal_price) / point     positive = received less

Deviation decomposition (buy, exactly additive):
    actual − theoretical = (deal − ask) + (ask − bid) + (bid − theoretical)
                           └ slippage ┘   └ actual spread ┘  └ request drift ┘
For sells the fill side is the bid, so the spread term is zero and the gap
splits into slippage + drift only.  ``theoretical`` is the zero-slippage
standard-tier fill on the signal bar:  buy = close + bar_spread, sell = close.

Paper trades are deliberately excluded: paper slippage IS the configured
value, so it carries no calibration signal.
"""

from __future__ import annotations

import math
import uuid
from decimal import Decimal
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LiveRun, LiveTrade, MT5Account

MIN_CALIBRATION_SAMPLES = 5

VERDICT_ALIGNED = "aligned"
VERDICT_MODERATE = "moderate"
VERDICT_DIVERGENT = "divergent"
VERDICT_INSUFFICIENT = "insufficient"


def _dec(v: float | Decimal | int | None, places: int = 6) -> Decimal | None:
    if v is None:
        return None
    return Decimal(str(round(float(v), places)))


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile over an ascending list (0<=q<=100)."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_vals[0]
    pos = (n - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _symbol_metrics(run: LiveRun) -> tuple[Decimal, Decimal]:
    """(point, contract_size) from the run's frozen symbol_info.

    Double-name fallback: MetaTrader5 5.0.6231 renamed contract-size fields
    (trade_contract_size vs contract_size) — getattr on the wrong name
    silently yields 0 and corrupts every cost figure.
    """
    snap = run.config_snapshot or {}
    info = snap.get("symbol_info") or {}
    point = float(info.get("point") or 0.0)
    cs = float(
        info.get("trade_contract_size") or info.get("contract_size") or 0.0
    )
    return Decimal(str(point)), Decimal(str(cs))


def _adverse_slippage_points(deal: LiveTrade, point: Decimal) -> float | None:
    """Signed adverse slippage in points for one deal (None = no snapshot)."""
    if deal.bid_at_request is None or deal.ask_at_request is None:
        return None
    price = float(deal.price)
    bid = float(deal.bid_at_request)
    ask = float(deal.ask_at_request)
    pt = float(point)
    if pt <= 0:
        return None
    if deal.direction == "buy":
        return (price - ask) / pt
    return (bid - price) / pt


def _deviation_row(
    deal: LiveTrade, point: Decimal, contract_size: Decimal
) -> dict | None:
    """One deal's actual-vs-theoretical decomposition (None = not comparable)."""
    if deal.signal_price is None or deal.signal_spread_points is None:
        return None
    pt = float(point)
    if pt <= 0:
        return None
    sig = float(deal.signal_price)
    sig_spread = float(deal.signal_spread_points)
    price = float(deal.price)
    theoretical = sig + sig_spread * pt if deal.direction == "buy" else sig
    slip = _adverse_slippage_points(deal, point)
    slip_out: float | None = None
    actual_spread_pts: float | None = None
    if deal.direction == "buy":
        # exact additive split: slip + spread + drift == actual - theoretical
        total_pts = (price - theoretical) / pt
        if slip is None:
            drift_pts = total_pts  # degrade: fold everything into drift
        else:
            slip_out = slip
            bid = float(deal.bid_at_request)
            ask = float(deal.ask_at_request)
            actual_spread_pts = (ask - bid) / pt
            drift_pts = (bid - theoretical) / pt
    else:
        # sell fills at bid — no explicit spread cost; total = slip + drift
        if slip is None:
            slip_pts = 0.0
            drift_pts = (theoretical - price) / pt
        else:
            slip_pts = slip
            bid = float(deal.bid_at_request)
            drift_pts = (theoretical - bid) / pt
        slip_out = slip_pts
        total_pts = slip_pts + drift_pts
    vol = float(deal.volume)
    cost = Decimal(str(round(total_pts * pt * float(contract_size) * vol, 4)))
    latency = None
    if deal.signal_bar_time is not None and deal.open_time is not None:
        latency = (deal.open_time - deal.signal_bar_time).total_seconds()
    return {
        "deal_ticket": deal.deal_ticket,
        "direction": deal.direction,
        "entry": deal.entry,
        "volume": deal.volume,
        "open_time": deal.open_time,
        "signal_bar_time": deal.signal_bar_time,
        "signal_price": deal.signal_price,
        "signal_spread_points": deal.signal_spread_points,
        "theoretical_price": _dec(theoretical, 8),
        "actual_price": deal.price,
        "total_deviation_points": _dec(total_pts),
        "slippage_points": _dec(slip_out),
        "actual_spread_points": _dec(actual_spread_pts)
        if actual_spread_pts is not None
        else None,
        "drift_points": _dec(drift_pts),
        "latency_seconds": round(latency, 3) if latency is not None else None,
        "cost": cost,
    }


async def calibration_for_symbol(
    db: AsyncSession, user_id: uuid.UUID, symbol: str
) -> dict:
    """Aggregate real-fill slippage for all of a user's live runs on a symbol."""
    rows = (
        await db.execute(
            select(LiveTrade, LiveRun)
            .join(LiveRun, LiveTrade.run_id == LiveRun.id)
            .where(
                LiveRun.user_id == user_id,
                LiveRun.symbol == symbol,
            )
            .order_by(LiveTrade.open_time.asc())
        )
    ).all()

    points: list[float] = []
    net_cost = 0.0
    skipped = 0
    first_t = last_t = None
    per_account: dict[uuid.UUID, dict] = {}
    point_value: Decimal | None = None
    for deal, run in rows:
        if first_t is None:
            first_t = deal.open_time
        last_t = deal.open_time
        pt, cs = _symbol_metrics(run)
        if point_value is None and pt > 0:
            point_value = pt
        slip = _adverse_slippage_points(deal, pt)
        if slip is None:
            skipped += 1
            continue
        points.append(slip)
        acc = per_account.setdefault(
            run.account_id, {"run_ids": set(), "samples": 0}
        )
        acc["run_ids"].add(run.id)
        acc["samples"] += 1
        if pt > 0 and cs > 0:
            net_cost += slip * float(pt) * float(cs) * float(deal.volume)

    insufficient = len(points) < MIN_CALIBRATION_SAMPLES
    ordered = sorted(points)
    p95 = _percentile(ordered, 95) if ordered else None
    recommended = (
        Decimal(str(max(0, math.ceil(p95)))) if p95 is not None and not insufficient else None
    )
    sources: list[dict] = []
    if per_account:
        acc_ids = list(per_account.keys())
        accts = (
            await db.execute(
                select(MT5Account).where(MT5Account.id.in_(acc_ids))
            )
        ).scalars().all()
        label_by_id = {
            a.id: f"{a.login} · {a.server}" if a.server else f"{a.login}"
            for a in accts
        }
        for aid, info in per_account.items():
            sources.append({
                "account_id": str(aid),
                "account_label": label_by_id.get(aid, str(aid)[:8]),
                "run_count": len(info["run_ids"]),
                "sample_count": info["samples"],
            })
        sources.sort(key=lambda s: s["sample_count"], reverse=True)

    mean = sum(ordered) / len(ordered) if ordered else None
    return {
        "symbol": symbol,
        "sample_count": len(points),
        "skipped_count": skipped,
        "mean_points": _dec(mean),
        "median_points": _dec(_percentile(ordered, 50)) if ordered else None,
        "p75_points": _dec(_percentile(ordered, 75)) if ordered else None,
        "p95_points": _dec(p95) if p95 is not None else None,
        "max_points": _dec(ordered[-1]) if ordered else None,
        "recommended_points": recommended,
        "slippage_cost": _dec(net_cost, 4) or Decimal("0"),
        "first_deal_time": first_t,
        "last_deal_time": last_t,
        "insufficient": insufficient,
        "sources": sources,
    }


def _verdict(avg_pts: float | None, sample_count: int) -> str:
    if sample_count < MIN_CALIBRATION_SAMPLES or avg_pts is None:
        return VERDICT_INSUFFICIENT
    if avg_pts <= 2.0:
        return VERDICT_ALIGNED
    if avg_pts <= 10.0:
        return VERDICT_MODERATE
    return VERDICT_DIVERGENT


async def deviation_report(db: AsyncSession, run_id: uuid.UUID) -> dict:
    """Compute the actual-vs-theoretical execution report for one live run."""
    run = await db.get(LiveRun, run_id)
    assert run is not None  # endpoint ownership check ran before us
    point, contract_size = _symbol_metrics(run)
    deals = (
        await db.execute(
            select(LiveTrade)
            .where(LiveTrade.run_id == run_id)
            .order_by(LiveTrade.open_time.asc())
        )
    ).scalars().all()

    rows: list[dict] = []
    skipped = 0
    for deal in deals:
        row = _deviation_row(deal, point, contract_size)
        if row is None:
            skipped += 1
        else:
            rows.append(row)

    totals = [float(r["total_deviation_points"]) for r in rows]
    slips = [
        float(r["slippage_points"]) for r in rows if r["slippage_points"] is not None
    ]
    spreads = [
        float(r["actual_spread_points"])
        for r in rows
        if r["actual_spread_points"] is not None
    ]
    sig_spreads = [
        float(r["signal_spread_points"])
        for r in rows
        if r["signal_spread_points"] is not None
    ]
    latencies = [
        float(r["latency_seconds"]) for r in rows if r["latency_seconds"] is not None
    ]
    total_cost = sum(float(r["cost"]) for r in rows)
    avg_total = sum(totals) / len(totals) if totals else None
    ordered = sorted(totals)
    return {
        "run_id": str(run_id),
        "symbol": run.symbol,
        "point": point,
        "sample_count": len(rows),
        "skipped_count": skipped,
        "avg_total_deviation_points": _dec(avg_total),
        "median_total_deviation_points": _dec(_percentile(ordered, 50))
        if ordered else None,
        "p95_total_deviation_points": _dec(_percentile(ordered, 95))
        if ordered else None,
        "avg_slippage_points": _dec(sum(slips) / len(slips)) if slips else None,
        "avg_signal_spread_points": _dec(sum(sig_spreads) / len(sig_spreads))
        if sig_spreads else None,
        "avg_actual_spread_points": _dec(sum(spreads) / len(spreads))
        if spreads else None,
        "avg_latency_seconds": round(sum(latencies) / len(latencies), 3)
        if latencies else None,
        "total_deviation_cost": _dec(total_cost, 4) or Decimal("0"),
        "verdict": _verdict(avg_total, len(rows)),
        "trades": rows,
    }
