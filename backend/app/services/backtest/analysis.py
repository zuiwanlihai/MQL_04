"""Overfit-guard analysis services (PRD Phase 2, 回测中心 P1).

Two analysis kinds hang off a completed source backtest task:

- **Monte Carlo** (:func:`run_monte_carlo`) — pure post-processing of the
  realised trade PnL sequence.  ``bootstrap`` resamples trades *with*
  replacement (final-return distribution + path risk), ``shuffle``
  permutes the order (path risk only — same trade set).  Seeded for
  reproducibility, chunk-vectorised with numpy to bound memory.

- **Walk-Forward** (:func:`build_walk_forward_tasks` /
  :func:`aggregate_walk_forward`) — splits the source replay window into
  ``folds + 1`` equal segments; every fold backtests the *frozen* strategy
  on an in-sample (IS) and the adjacent out-of-sample (OOS) window as real
  child ``BacktestTask`` rows executed by the existing queue.  Aggregation
  runs on read once all children are terminal (compute-on-read, cached).

- **Parameter Optimization** (:func:`expand_grid` / :func:`sample_random` /
  :func:`build_optimization_children` / :func:`aggregate_optimization`) —
  grid (cartesian product) or seeded random sampling over a user-declared
  parameter space; every combination becomes one real child ``BacktestTask``
  whose ``parameters`` merge the source baseline with the combo override.
  Aggregation on read ranks combinations by the chosen objective metric.
"""

from __future__ import annotations

import math
import uuid as uuid_mod
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BacktestAnalysis, BacktestReport, BacktestTask, BacktestTrade
from app.models.analysis import ANALYSIS_COMPLETED, ANALYSIS_FAILED, ANALYSIS_RUNNING

# Monte Carlo bounds (chunk sims to keep peak allocation ≈ sims_chunk × trades)
_MC_SIMS_MIN, _MC_SIMS_MAX = 100, 10_000
_MC_SIM_CHUNK = 250
_MC_TRADES_MAX = 5000

# Walk-Forward bounds
_WF_FOLDS_MIN, _WF_FOLDS_MAX = 2, 8

# Robustness rule: ≥70 % of folds profitable out-of-sample → 稳健
_WF_ROBUST_RATIO = 0.7


class AnalysisValidationError(Exception):
    """Raised for pre-flight analysis errors mapped to HTTP 422/409."""


# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------

def _percentiles(arr: np.ndarray) -> dict[str, float]:
    ps = np.percentile(arr, [5, 25, 50, 75, 95])
    return {
        "p5": round(float(ps[0]), 4),
        "p25": round(float(ps[1]), 4),
        "p50": round(float(ps[2]), 4),
        "p75": round(float(ps[3]), 4),
        "p95": round(float(ps[4]), 4),
        "mean": round(float(arr.mean()), 4),
    }


def _path_metrics(profit_matrix: np.ndarray, initial: float) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised final-return % and max-DD % per simulation row.

    Equity starts at ``initial``; drawdown percent follows the engine's
    convention (peak − trough) / peak × 100 (see sandbox/metrics.py).
    """
    equity = initial + np.cumsum(profit_matrix, axis=1)
    final = equity[:, -1]
    final_ret = (final - initial) / initial * 100.0
    peaks = np.maximum.accumulate(equity, axis=1)
    dd_pct = np.where(
        peaks > 0, (peaks - equity) / np.where(peaks > 0, peaks, 1.0) * 100.0, 0.0
    ).max(axis=1)
    return final_ret, dd_pct


def run_monte_carlo(
    *,
    initial_deposit: float,
    report: BacktestReport,
    profits: list[float],
    simulations: int,
    method: str,
    ruin_dd_percent: float,
    seed: int,
) -> dict:
    """Run the resampling study; returns the cached ``result`` payload."""
    n = len(profits)
    if n < 5:
        raise AnalysisValidationError("成交笔数不足（至少 5 笔平仓交易）才能做蒙特卡洛模拟")
    if not _MC_SIMS_MIN <= simulations <= _MC_SIMS_MAX:
        raise AnalysisValidationError(
            f"模拟次数必须在 {_MC_SIMS_MIN}~{_MC_SIMS_MAX} 之间"
        )
    if n > _MC_TRADES_MAX:
        profits = profits[:_MC_TRADES_MAX]
        n = _MC_TRADES_MAX

    rng = np.random.default_rng(seed)
    p = np.asarray(profits, dtype=np.float64)
    initial = float(initial_deposit)

    final_d = np.empty(simulations, dtype=np.float64)
    dd_d = np.empty(simulations, dtype=np.float64)
    done = 0
    while done < simulations:
        k = min(_MC_SIM_CHUNK, simulations - done)
        if method == "shuffle":
            # Permutation per row: same trade set, random order.
            idx = np.argsort(rng.random((k, n)), axis=1)
            sample = p[idx]
        else:  # bootstrap — with replacement
            sample = p[rng.integers(0, n, size=(k, n))]
        fr, dd = _path_metrics(sample, initial)
        final_d[done:done + k] = fr
        dd_d[done:done + k] = dd
        done += k

    prob_loss = float((final_d < 0).mean())
    prob_ruin = float((dd_d > ruin_dd_percent).mean())
    return {
        "method": method,
        "simulations": simulations,
        "ruin_dd_percent": ruin_dd_percent,
        "seed": seed,
        "original": {
            "final_return_percent": float(report.total_return_percent),
            "max_dd_percent": float(report.max_drawdown_percent),
            "trades": int(report.total_trades),
        },
        "final_return_percent": _percentiles(final_d),
        "max_dd_percent": _percentiles(dd_d),
        "prob_loss": round(prob_loss, 4),
        "prob_ruin": round(prob_ruin, 4),
    }


# ---------------------------------------------------------------------------
# Walk-Forward
# ---------------------------------------------------------------------------

def split_walk_forward_windows(
    start: datetime, end: datetime, folds: int, mode: str
) -> list[tuple[datetime, datetime, datetime, datetime]]:
    """Return ``[(is_start, is_end, oos_start, oos_end), …]`` per fold.

    The replay range is cut into ``folds + 1`` equal segments.  Rolling mode:
    IS = segment i, OOS = segment i+1.  Anchored mode: IS = segments 0..i
    (growing window), OOS = segment i+1.
    """
    total = (end - start).total_seconds()
    seg = total / (folds + 1)
    out: list[tuple[datetime, datetime, datetime, datetime]] = []
    for i in range(folds):
        seg_start = start + timedelta(seconds=seg * i)
        seg_end = start + timedelta(seconds=seg * (i + 1))
        oos_end = start + timedelta(seconds=seg * (i + 2))
        if mode == "anchored":
            out.append((start, seg_end, seg_end, oos_end))
        else:  # rolling
            out.append((seg_start, seg_end, seg_end, oos_end))
    return out


def build_walk_forward_children(
    src: BacktestTask, analysis_id: uuid_mod.UUID, folds: int, mode: str
) -> tuple[list[BacktestTask], list[dict]]:
    """Instantiate 2×folds child tasks mirroring the frozen source config.

    Returns the ORM rows (not yet persisted) and the params-side window
    mapping ``[{role, fold, start, end}, …]`` kept for aggregation.
    """
    windows = split_walk_forward_windows(src.start_date, src.end_date, folds, mode)
    children: list[BacktestTask] = []
    mapping: list[dict] = []
    for fold_i, (is_s, is_e, oos_s, oos_e) in enumerate(windows, start=1):
        for role, s, e in (("is", is_s, is_e), ("oos", oos_s, oos_e)):
            snap = dict(src.config_snapshot or {})
            snap["seed"] = None  # queue worker assigns a fresh random seed
            snap["analysis_id"] = str(analysis_id)
            snap["analysis_role"] = f"wf_{role}_{fold_i}"
            snap["wf_mode"] = mode
            child = BacktestTask(
                user_id=src.user_id,
                strategy_id=src.strategy_id,
                source_code=src.source_code,
                execution_mode=src.execution_mode,
                symbol=src.symbol,
                timeframe=src.timeframe,
                start_date=s,
                end_date=e,
                initial_deposit=src.initial_deposit,
                leverage=src.leverage,
                strategy_version=src.strategy_version,
                spread_mode=src.spread_mode,
                spread_points=src.spread_points,
                commission_per_lot=src.commission_per_lot,
                slippage_points=src.slippage_points,
                warmup_bars=src.warmup_bars,
                parameters=src.parameters,
                config_snapshot=snap,
                group_id=None,
                created_by=src.created_by or src.user_id,
            )
            children.append(child)
            mapping.append({
                "role": role,
                "fold": fold_i,
                "start": s.isoformat(),
                "end": e.isoformat(),
            })
    return children, mapping


async def aggregate_walk_forward(
    db: AsyncSession, analysis: BacktestAnalysis
) -> Optional[BacktestAnalysis]:
    """Compute-on-read aggregation for a running WF analysis.

    Returns the analysis after transition: stays ``running`` while children
    are pending, becomes ``completed`` (result cached) when all children
    finished, or ``failed`` when any child failed/cancelled/was deleted.
    """
    mapping = analysis.params.get("children") or []
    child_ids = [uuid_mod.UUID(m["task_id"]) for m in mapping]
    rows = {
        t.id: t
        for t in await db.scalars(select(BacktestTask).where(BacktestTask.id.in_(child_ids)))
    }
    terminal = {"completed", "failed", "cancelled"}
    missing = [m for m in mapping if uuid_mod.UUID(m["task_id"]) not in rows]
    if missing:
        analysis.status = ANALYSIS_FAILED
        analysis.error = f"子回测任务缺失（可能已被删除）：{len(missing)} 个"
        db.add(analysis)
        await db.commit()
        await db.refresh(analysis)
        return analysis

    tasks = list(rows.values())
    if any(t.status not in terminal for t in tasks):
        return analysis  # still running
    bad = [t for t in tasks if t.status != "completed"]
    if bad:
        t0 = bad[0]
        analysis.status = ANALYSIS_FAILED
        analysis.error = (
            f"Walk-Forward 子任务失败：{len(bad)}/{len(tasks)} 个未完成，"
            f"首个失败原因：{t0.error_message or t0.status}"
        )
        db.add(analysis)
        await db.commit()
        await db.refresh(analysis)
        return analysis

    by_key = {
        (t.config_snapshot or {}).get("analysis_role"): t.id for t in tasks
    }
    reports = {
        r.task_id: r
        for r in await db.scalars(
            select(BacktestReport).where(
                BacktestReport.task_id.in_([t.id for t in tasks])
            )
        )
    }

    folds_n = int(analysis.params.get("folds", 0))
    rows_out: list[dict] = []
    eff_list: list[float] = []
    oos_returns: list[float] = []
    profitable = 0
    for fold_i in range(1, folds_n + 1):
        is_id = by_key.get(f"wf_is_{fold_i}")
        oos_id = by_key.get(f"wf_oos_{fold_i}")
        if is_id is None or oos_id is None:
            continue
        ir, orr = reports.get(is_id), reports.get(oos_id)
        if ir is None or orr is None:
            continue
        is_ret = float(ir.total_return_percent)
        oos_ret = float(orr.total_return_percent)
        eff: float | None = None
        if abs(is_ret) > 1e-9 and is_ret > 0:
            eff = round(oos_ret / is_ret, 4)
            if eff is not None:
                eff_list.append(eff)
        oos_returns.append(oos_ret)
        if oos_ret > 0:
            profitable += 1
        rows_out.append({
            "fold": fold_i,
            "is_task_id": str(is_id),
            "oos_task_id": str(oos_id),
            "is_return_percent": round(is_ret, 4),
            "oos_return_percent": round(oos_ret, 4),
            "is_max_dd_percent": round(float(ir.max_drawdown_percent), 4),
            "oos_max_dd_percent": round(float(orr.max_drawdown_percent), 4),
            "is_sharpe": round(float(ir.sharpe_ratio), 4),
            "oos_sharpe": round(float(orr.sharpe_ratio), 4),
            "oos_trades": int(orr.total_trades),
            "efficiency": eff,
        })

    n = len(oos_returns)
    analysis.status = ANALYSIS_COMPLETED
    analysis.result = {
        "mode": analysis.params.get("mode", "rolling"),
        "folds": n,
        "rows": rows_out,
        "summary": {
            "oos_profitable": profitable,
            "oos_median_return_percent": (
                round(float(np.median(oos_returns)), 4) if n else None
            ),
            "oos_mean_return_percent": (
                round(float(np.mean(oos_returns)), 4) if n else None
            ),
            "wf_efficiency_mean": (
                round(float(np.mean(eff_list)), 4) if eff_list else None
            ),
            "robust": n > 0 and profitable >= math.ceil(n * _WF_ROBUST_RATIO),
        },
    }
    db.add(analysis)
    await db.commit()
    await db.refresh(analysis)
    return analysis


# ---------------------------------------------------------------------------
# Parameter Optimization (grid / random)
# ---------------------------------------------------------------------------

# Hard cap on grid combinations per optimization run (serial queue).
_OPT_MAX_COMBOS = 100

# Report columns eligible as the ranking objective.
_OPT_OBJECTIVES = frozenset({
    "sharpe_ratio", "annual_return_percent", "total_return_percent",
    "calmar_ratio", "profit_factor",
})


def expand_grid(space: list[tuple[str, list]]) -> list[dict]:
    """Cartesian product over ``[(name, values), …]`` in row-major order."""
    combos: list[dict] = [{}]
    for name, values in space:
        combos = [{**c, name: v} for c in combos for v in values]
    return combos


def sample_random(
    space: list[tuple[str, list]], samples: int, seed: int
) -> list[dict]:
    """Seeded random sample *without replacement* from the full grid.

    ``samples >= |grid|`` degenerates to the full grid (order shuffled by
    the seed so child-task numbering is not correlated with grid order).
    """
    combos = expand_grid(space)
    if samples >= len(combos):
        rng = np.random.default_rng(seed)
        order = rng.permutation(len(combos))
        return [combos[int(i)] for i in order]
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(combos), size=samples, replace=False)
    return [combos[int(i)] for i in sorted(idx)]


def build_optimization_children(
    src: BacktestTask,
    analysis_id: uuid_mod.UUID,
    combos: list[dict],
) -> tuple[list[BacktestTask], list[dict]]:
    """One child task per parameter combination, mirroring the frozen config.

    ``parameters`` merge the source baseline with the combo override so an
    omitted key keeps its baseline value.  Returns ORM rows (not persisted)
    and the params-side mapping ``[{index, params}, …]``.
    """
    children: list[BacktestTask] = []
    mapping: list[dict] = []
    for i, combo in enumerate(combos):
        snap = dict(src.config_snapshot or {})
        snap["seed"] = None  # queue worker assigns a fresh random seed
        snap["analysis_id"] = str(analysis_id)
        snap["analysis_role"] = f"opt_{i}"
        snap["opt_combo"] = combo
        child = BacktestTask(
            user_id=src.user_id,
            strategy_id=src.strategy_id,
            source_code=src.source_code,
            execution_mode=src.execution_mode,
            symbol=src.symbol,
            timeframe=src.timeframe,
            start_date=src.start_date,
            end_date=src.end_date,
            initial_deposit=src.initial_deposit,
            leverage=src.leverage,
            strategy_version=src.strategy_version,
            spread_mode=src.spread_mode,
            spread_points=src.spread_points,
            commission_per_lot=src.commission_per_lot,
            slippage_points=src.slippage_points,
            warmup_bars=src.warmup_bars,
            parameters={**src.parameters, **combo},
            config_snapshot=snap,
            group_id=None,
            created_by=src.created_by or src.user_id,
        )
        children.append(child)
        mapping.append({"index": i, "params": combo})
    return children, mapping


async def aggregate_optimization(
    db: AsyncSession, analysis: BacktestAnalysis
) -> Optional[BacktestAnalysis]:
    """Compute-on-read aggregation for a running optimization analysis.

    Same convention as Walk-Forward: stays ``running`` while children are
    pending, ``completed`` (ranking cached) when every child completed, or
    ``failed`` when any child failed/cancelled/was deleted.
    """
    mapping = analysis.params.get("children") or []
    child_ids = [uuid_mod.UUID(m["task_id"]) for m in mapping]
    rows = {
        t.id: t
        for t in await db.scalars(select(BacktestTask).where(BacktestTask.id.in_(child_ids)))
    }
    terminal = {"completed", "failed", "cancelled"}
    missing = [m for m in mapping if uuid_mod.UUID(m["task_id"]) not in rows]
    if missing:
        analysis.status = ANALYSIS_FAILED
        analysis.error = f"子回测任务缺失（可能已被删除）：{len(missing)} 个"
        db.add(analysis)
        await db.commit()
        await db.refresh(analysis)
        return analysis

    tasks = list(rows.values())
    if any(t.status not in terminal for t in tasks):
        return analysis  # still running
    bad = [t for t in tasks if t.status != "completed"]
    if bad:
        t0 = bad[0]
        analysis.status = ANALYSIS_FAILED
        analysis.error = (
            f"参数优化子任务失败：{len(bad)}/{len(tasks)} 个未完成，"
            f"首个失败原因：{t0.error_message or t0.status}"
        )
        db.add(analysis)
        await db.commit()
        await db.refresh(analysis)
        return analysis

    reports = {
        r.task_id: r
        for r in await db.scalars(
            select(BacktestReport).where(
                BacktestReport.task_id.in_([t.id for t in tasks])
            )
        )
    }
    objective = analysis.params.get("objective", "sharpe_ratio")

    rows_out: list[dict] = []
    for m in mapping:
        t = rows[uuid_mod.UUID(m["task_id"])]
        r = reports.get(t.id)
        value: float | None = None
        if r is not None:
            value = round(float(getattr(r, objective)), 4)
        rows_out.append({
            "index": int(m["index"]),
            "task_id": m["task_id"],
            "params": m["params"],
            "objective": value,
            "total_return_percent": (
                round(float(r.total_return_percent), 4) if r else None
            ),
            "max_drawdown_percent": (
                round(float(r.max_drawdown_percent), 4) if r else None
            ),
            "sharpe_ratio": round(float(r.sharpe_ratio), 4) if r else None,
            "total_trades": int(r.total_trades) if r else None,
        })

    # Descending by objective; nulls (shouldn't happen post-guard) last.
    rows_out.sort(key=lambda x: (x["objective"] is None, -(x["objective"] or 0.0)))
    analysis.status = ANALYSIS_COMPLETED
    analysis.result = {
        "method": analysis.params.get("method", "grid"),
        "objective": objective,
        "rows": rows_out,
        "best": rows_out[0] if rows_out else None,
    }
    db.add(analysis)
    await db.commit()
    await db.refresh(analysis)
    return analysis


async def load_source_trades(
    db: AsyncSession, task_id: uuid_mod.UUID
) -> list[float]:
    """Closed-trade profits of a completed task, open-time ordered."""
    rows = await db.scalars(
        select(BacktestTrade.profit)
        .where(BacktestTrade.task_id == task_id, BacktestTrade.close_time.isnot(None))
        .order_by(BacktestTrade.open_time, BacktestTrade.ticket)
    )
    return [float(v) for v in rows]


def new_analysis_row(
    *, user_id: uuid_mod.UUID, task_id: uuid_mod.UUID, kind: str, params: dict
) -> BacktestAnalysis:
    return BacktestAnalysis(
        user_id=user_id,
        task_id=task_id,
        kind=kind,
        status=ANALYSIS_RUNNING,
        params=params,
    )
