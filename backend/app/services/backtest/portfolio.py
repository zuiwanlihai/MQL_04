"""Portfolio aggregation (PRD Phase 3 P2): multi-symbol combined equity.

Compute-on-read pattern (same as Walk-Forward / optimization): while
children run the portfolio stays ``running``; once every child is terminal
the combined equity curve is built and the metrics are cached into
``result``.  Per-symbol curves are down-sampled (≤1500 points each) — the
combined metrics are therefore curve-approximations, computed on the union
timeline with per-symbol forward-fill.

Pure helpers (``merge_equity_curves`` / ``compute_portfolio_metrics``) are
unit-testable without a database.
"""

from __future__ import annotations

import math
import uuid as uuid_mod
from datetime import UTC, datetime
from statistics import mean, stdev

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import ANALYSIS_COMPLETED, ANALYSIS_FAILED
from app.models.backtest import BacktestReport, BacktestTask
from app.models.portfolio import PortfolioBacktest

_TERMINAL = frozenset({"completed", "failed", "cancelled"})


def merge_equity_curves(
    curves: dict[str, list[dict]], per_deposit: dict[str, float]
) -> list[dict]:
    """Combine per-symbol equity curves onto the union timeline.

    Each symbol's equity is forward-filled from its latest point at or
    before ``t`` (before its first point it sits at its starting deposit).
    Combined ``drawdown`` is recomputed from the running peak of the summed
    equity; ``baseline`` is the sum of the forward-filled per-symbol
    baselines.  ``time`` strings are ISO-8601 (lexicographic == chronological).
    """
    symbols = list(curves.keys())
    idx = {s: 0 for s in symbols}
    last_eq = {s: float(per_deposit.get(s, 0.0)) for s in symbols}
    last_bl = {s: float(per_deposit.get(s, 0.0)) for s in symbols}
    times = sorted({p["time"] for pts in curves.values() for p in pts if pts})

    out: list[dict] = []
    peak = 0.0
    for t in times:
        total_eq = 0.0
        total_bl = 0.0
        for s in symbols:
            pts = curves[s]
            i = idx[s]
            while i < len(pts) and pts[i]["time"] <= t:
                last_eq[s] = float(pts[i]["equity"])
                last_bl[s] = float(pts[i]["baseline"])
                i += 1
            idx[s] = i
            total_eq += last_eq[s]
            total_bl += last_bl[s]
        peak = max(peak, total_eq)
        dd = (peak - total_eq) / peak * 100 if peak > 0 else 0.0
        out.append({
            "time": t,
            "equity": round(total_eq, 2),
            "balance": round(total_eq, 2),
            "drawdown": round(dd, 4),
            "baseline": round(total_bl, 2),
        })
    return out


def compute_portfolio_metrics(
    equity: list[dict], total_deposit: float, start: datetime, end: datetime
) -> dict:
    """Portfolio-level metrics from the merged (down-sampled) equity curve."""
    if not equity or total_deposit <= 0:
        return {
            "total_return": 0.0, "total_return_percent": 0.0,
            "annual_return_percent": 0.0, "max_drawdown_percent": 0.0,
            "max_drawdown_amount": 0.0, "sharpe_ratio": 0.0,
        }
    final = float(equity[-1]["equity"])
    total_return = final - total_deposit
    trp = total_return / total_deposit * 100

    peak = 0.0
    mdd = 0.0
    mdd_amt = 0.0
    for p in equity:
        eq = float(p["equity"])
        peak = max(peak, eq)
        dd_amt = peak - eq
        if peak > 0 and dd_amt / peak * 100 > mdd:
            mdd = dd_amt / peak * 100
            mdd_amt = dd_amt

    years = max((end - start).total_seconds() / 86400 / 365.0, 1e-9)
    growth = final / total_deposit
    if growth > 0:
        annual = (growth ** (1.0 / years) - 1.0) * 100
    else:
        annual = -100.0

    rets = []
    for a, b in zip(equity, equity[1:]):
        if float(a["equity"]) > 0:
            rets.append(float(b["equity"]) / float(a["equity"]) - 1.0)
    sharpe = 0.0
    if len(rets) > 1:
        sd = stdev(rets)
        if sd > 1e-12:
            span_s = max((end - start).total_seconds(), 1e-9)
            interval_s = span_s / (len(equity) - 1)
            factor = 365 * 86400 / interval_s
            sharpe = mean(rets) / sd * math.sqrt(factor)

    return {
        "total_return": round(total_return, 2),
        "total_return_percent": round(trp, 4),
        "annual_return_percent": round(annual, 4),
        "max_drawdown_percent": round(mdd, 4),
        "max_drawdown_amount": round(mdd_amt, 2),
        "sharpe_ratio": round(sharpe, 4),
    }


def per_symbol_rows(
    reports: dict[uuid_mod.UUID, BacktestReport],
    children: list[dict],
) -> list[dict]:
    """Per-symbol summary rows for the portfolio report (symbol order)."""
    out: list[dict] = []
    for m in children:
        r = reports.get(uuid_mod.UUID(m["task_id"]))
        if r is None:
            continue
        out.append({
            "symbol": m["symbol"],
            "task_id": m["task_id"],
            "deposit": m.get("deposit"),
            "total_return": round(float(r.total_return), 2),
            "total_return_percent": round(float(r.total_return_percent), 4),
            "max_drawdown_percent": round(float(r.max_drawdown_percent), 4),
            "sharpe_ratio": round(float(r.sharpe_ratio), 4),
            "total_trades": int(r.total_trades),
            "win_rate": round(float(r.win_rate), 4),
            "profit_factor": round(float(r.profit_factor), 4),
            "total_swap": round(float(r.total_swap), 2),
        })
    return out


async def aggregate_portfolio(
    db: AsyncSession, row: PortfolioBacktest
) -> PortfolioBacktest:
    """Compute-on-read aggregation for a running portfolio.

    Stays ``running`` while children are pending; becomes ``completed``
    (result cached) when all children finished, or ``failed`` when any
    child failed / was cancelled / was deleted.
    """
    mapping = row.params.get("children") or []
    child_ids = [uuid_mod.UUID(m["task_id"]) for m in mapping]
    tasks = {
        t.id: t
        for t in await db.scalars(
            select(BacktestTask).where(BacktestTask.id.in_(child_ids))
        )
    }
    missing = [m for m in mapping if uuid_mod.UUID(m["task_id"]) not in tasks]
    if missing:
        row.status = ANALYSIS_FAILED
        row.error = f"组合子回测任务缺失（可能已被删除）：{len(missing)} 个"
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    if any(t.status not in _TERMINAL for t in tasks.values()):
        return row  # still running
    bad = [t for t in tasks.values() if t.status != "completed"]
    if bad:
        t0 = bad[0]
        bad_symbols = [
            m["symbol"] for m in mapping
            if uuid_mod.UUID(m["task_id"]) in {t.id for t in bad}
        ]
        row.status = ANALYSIS_FAILED
        row.error = (
            f"组合子回测失败：{len(bad)}/{len(tasks)} 个未完成"
            f"（品种：{'、'.join(bad_symbols)}），"
            f"首个失败原因：{t0.error_message or t0.status}"
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    reports = {
        r.task_id: r
        for r in await db.scalars(
            select(BacktestReport).where(
                BacktestReport.task_id.in_([t.id for t in tasks.values()])
            )
        )
    }
    curves: dict[str, list[dict]] = {}
    per_deposit: dict[str, float] = {}
    for m in mapping:
        r = reports.get(uuid_mod.UUID(m["task_id"]))
        if r is None:
            continue
        curves[m["symbol"]] = list(r.equity_curve or [])
        per_deposit[m["symbol"]] = float(m.get("deposit") or 0.0)

    merged = merge_equity_curves(curves, per_deposit)
    start = _parse_iso(row.params.get("start"))
    end = _parse_iso(row.params.get("end"))
    metrics = compute_portfolio_metrics(merged, float(row.total_deposit), start, end)

    row.status = ANALYSIS_COMPLETED
    row.result = {
        "metrics": metrics,
        "equity_curve": merged,
        "symbols": per_symbol_rows(reports, mapping),
    }
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def _parse_iso(v: object) -> datetime:
    if isinstance(v, str):
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    if isinstance(v, datetime):
        return v
    return datetime.now(UTC)
