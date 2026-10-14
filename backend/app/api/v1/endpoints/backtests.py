"""Backtest center endpoints (M4).

Route order: the static ``/compare/{group_id}`` path is declared before the
``/{task_id}`` routes.  All queries are user-scoped (``user_id == sub``);
cross-owner access returns 404, never 403, to avoid task-id enumeration.

Submission freezes a reproducible ``config_snapshot`` (assumption text,
seed, tier, SDK/dependency versions); the queue worker later enriches it
with symbol_info / spread snapshot / data fingerprint.  ``sensitivity``
creates a paired conservative + standard run sharing one ``group_id``.
"""

from __future__ import annotations

import csv
import io
import json
import math
import random
import shutil
import sys
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import CurrentUser, DBSession
from app.models import (
    BacktestAnalysis,
    BacktestLog,
    BacktestReport,
    BacktestTask,
    BacktestTrade,
    KlineCache,
    PortfolioBacktest,
    Strategy,
    StrategyVersion,
)
from app.sandbox.payload import describe_execution_mode
from app.schemas.backtest import (
    AnalysisListResponse,
    AnalysisOut,
    BacktestCreate,
    BacktestListResponse,
    BacktestLogOut,
    BacktestRatingRequest,
    BacktestReportOut,
    BacktestTaskDetailOut,
    BacktestTaskOut,
    BacktestTradeOut,
    LogListResponse,
    MonteCarloCreate,
    OptimizationCreate,
    PortfolioCreate,
    PortfolioListResponse,
    PortfolioOut,
    SensitivityCompareOut,
    SensitivitySide,
    TradeListResponse,
    WalkForwardCreate,
)
from app.schemas.notebook import QueueOverviewOut
from app.schemas.slippage import SlippageCalibrationOut
from app.services import audit
from app.services.backtest.analysis import (
    AnalysisValidationError,
    aggregate_optimization,
    aggregate_walk_forward,
    build_optimization_children,
    build_walk_forward_children,
    expand_grid,
    load_source_trades,
    new_analysis_row,
    run_monte_carlo,
    sample_random,
)
from app.services.backtest.data_fetcher import estimate_bars
from app.services.backtest.portfolio import aggregate_portfolio
from app.services.backtest.queue import get_backtest_queue
from app.services.slippage import calibration_for_symbol

router = APIRouter()

_PAGE_SIZE_MAX = 100
_ANNUAL_DIFF_ALERT = 5.0  # percentage points → sensitivity warning

_TRADE_CSV_HEADER = [
    "ticket", "direction", "volume", "open_time", "open_price", "base_price",
    "close_time", "close_price", "profit", "profit_percent", "swap",
    "commission", "spread_cost", "slippage_cost", "comment", "is_forced",
]


def _user_id(current: dict) -> uuid.UUID:
    return uuid.UUID(current["sub"])


def _dependency_versions() -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version

    out: dict[str, str] = {"python": sys.version.split()[0], "platform": settings.APP_VERSION}
    for pkg in ("numpy", "pandas", "scipy"):
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:
            out[pkg] = "n/a"
    return out


async def _get_owned_task(db: AsyncSession, task_id: uuid.UUID, current: dict) -> BacktestTask:
    task = await db.get(BacktestTask, task_id)
    if task is None or str(task.user_id) != current["sub"]:
        raise HTTPException(status_code=404, detail="回测任务不存在")
    return task


async def _build_snapshot(payload: BacktestCreate, source: str, version: int | None) -> dict:
    """Freeze the assumption surface at submission time (reproducibility)."""
    return {
        "execution_mode": payload.execution_mode,
        "assumption_text": describe_execution_mode(payload.execution_mode),
        "assumption_ack": payload.assumption_ack,
        "seed": random.randrange(2**31),
        "spread_mode": payload.spread_mode,
        "spread_points": payload.spread_points,
        "slippage_points": payload.slippage_points,
        "commission_per_lot": payload.commission_per_lot,
        "warmup_bars": payload.warmup_bars,
        "strategy_version": version,
        "source_hash": _source_hash(source),
        "sdk": _dependency_versions(),
    }


def _source_hash(source: str) -> str:
    import hashlib

    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def _dec(v: float | None, places: int = 4) -> Decimal | None:
    return None if v is None else Decimal(str(round(float(v), places)))


def _new_task(user_id: uuid.UUID, payload: BacktestCreate, source: str, snapshot: dict,
              execution_mode: str, group_id: uuid.UUID | None) -> BacktestTask:
    return BacktestTask(
        user_id=user_id,
        strategy_id=payload.strategy_id,
        source_code=source,
        execution_mode=execution_mode,
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        start_date=payload.start_date,
        end_date=payload.end_date,
        initial_deposit=_dec(payload.initial_deposit) or Decimal("0"),
        leverage=payload.leverage,
        strategy_version=snapshot["strategy_version"],
        spread_mode=payload.spread_mode,
        spread_points=_dec(payload.spread_points, 2),
        commission_per_lot=_dec(payload.commission_per_lot) or Decimal("0"),
        slippage_points=_dec(payload.slippage_points, 2),
        warmup_bars=payload.warmup_bars,
        parameters=payload.parameters,
        data_source=payload.data_source,
        config_snapshot=snapshot,
        group_id=group_id,
        created_by=user_id,
    )


def _write_local_bars(task_id: uuid.UUID, payload: BacktestCreate) -> None:
    """data_source=local：把请求携带的缓存K线落到任务数据目录（worker 读取）。"""
    rows = [
        b.model_dump() for b in (payload.local_bars or [])
    ]
    pdir = Path(settings.BACKTEST_RUNTIME_DIR) / str(task_id)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "local_bars.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Create (POST /backtests)
# ---------------------------------------------------------------------------

@router.post("", response_model=list[BacktestTaskOut], status_code=status.HTTP_201_CREATED)
async def create_backtest(
    payload: BacktestCreate, current: CurrentUser, db: DBSession
) -> list[BacktestTask]:
    user_id = _user_id(current)
    strategy = await db.get(Strategy, payload.strategy_id)
    if strategy is None or str(strategy.user_id) != current["sub"]:
        raise HTTPException(status_code=404, detail="策略不存在")
    if strategy.status == "archived":
        raise HTTPException(status_code=409, detail="已归档策略不可回测")

    # Resolve source snapshot: frozen version vs working copy.
    version = payload.version
    if version is None:
        source = strategy.source_code
    else:
        vrow = (
            await db.execute(
                select(StrategyVersion).where(
                    StrategyVersion.strategy_id == strategy.id,
                    StrategyVersion.version == version,
                )
            )
        ).scalar_one_or_none()
        if vrow is None:
            raise HTTPException(status_code=404, detail=f"版本 v{version} 不存在")
        source = vrow.source_code

    if not source.strip():
        raise HTTPException(status_code=422, detail="策略代码为空，无法回测")

    # 本地数据来源：请求携带缓存K线（≥50根）或服务器 kline_cache 表有该条均可；
    # worker 优先直读共享缓存表，旧任务回退到任务目录的 local_bars.json。
    # 缓存按模拟/实盘隔离，同名品种可能两行并存，任一盘数据充足即可创建
    if payload.data_source == "local":
        counts = (
            await db.execute(
                select(KlineCache.bar_count).where(
                    KlineCache.symbol == payload.symbol.upper(),
                    KlineCache.timeframe == payload.timeframe,
                )
            )
        ).scalars().all()
        cached_n = max(counts, default=0)
        if (not payload.local_bars or len(payload.local_bars) < 50) and cached_n < 50:
            raise HTTPException(
                status_code=422,
                detail="本地缓存K线不足（至少50根）：请先在本地K线管理下载数据，或改用服务器数据",
            )

    # Pre-enqueue bar-count guard (no gateway round-trip).
    est = estimate_bars(
        payload.start_date, payload.end_date, payload.timeframe, payload.warmup_bars
    )
    if est > settings.BACKTEST_MAX_BARS:
        raise HTTPException(
            status_code=422,
            detail=f"预估 K 线数 {est} 超过上限 {settings.BACKTEST_MAX_BARS}，请缩小区间或提高周期",
        )

    snapshot = await _build_snapshot(payload, source, version)

    queue = get_backtest_queue()
    if payload.sensitivity:
        group_id = uuid.uuid4()
        modes = ["conservative", "standard"]
    else:
        group_id = None
        modes = [payload.execution_mode]

    created: list[BacktestTask] = []
    for mode in modes:
        snap = dict(snapshot)
        snap["execution_mode"] = mode
        snap["assumption_text"] = describe_execution_mode(mode)
        task = _new_task(user_id, payload, source, snap, mode, group_id)
        db.add(task)
        created.append(task)
    try:
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"任务创建失败：{exc}") from None
    for task in created:
        await db.refresh(task)
        if payload.data_source == "local":
            _write_local_bars(task.id, payload)
        if settings.BACKTEST_ENABLED:
            queue.enqueue(str(task.id))
    return created


# ---------------------------------------------------------------------------
# List (GET /backtests)
# ---------------------------------------------------------------------------

@router.get("", response_model=BacktestListResponse)
async def list_backtests(
    current: CurrentUser,
    db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=_PAGE_SIZE_MAX),
    strategy_id: uuid.UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
) -> BacktestListResponse:
    filters = [BacktestTask.user_id == _user_id(current)]
    if strategy_id is not None:
        filters.append(BacktestTask.strategy_id == strategy_id)
    if status_filter:
        if status_filter not in (
            "pending", "running", "cancelling", "completed", "failed", "cancelled"
        ):
            raise HTTPException(status_code=422, detail="无效的状态筛选值")
        filters.append(BacktestTask.status == status_filter)

    total = (
        await db.execute(select(func.count()).select_from(BacktestTask).where(*filters))
    ).scalar_one()
    result = await db.execute(
        select(BacktestTask)
        .where(*filters)
        .order_by(BacktestTask.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list(result.scalars())
    return BacktestListResponse(
        items=[BacktestTaskOut.model_validate(r) for r in items],
        total=total, page=page, page_size=page_size,
        total_pages=max(math.ceil(total / page_size), 1),
    )


# ---------------------------------------------------------------------------
# Sensitivity compare (static path — declared before /{task_id})
# ---------------------------------------------------------------------------

@router.get("/compare/{group_id}", response_model=SensitivityCompareOut)
async def compare_group(
    group_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> SensitivityCompareOut:
    rows = await db.execute(
        select(BacktestTask).where(
            BacktestTask.group_id == group_id,
            BacktestTask.user_id == _user_id(current),
        )
    )
    tasks = {t.execution_mode: t for t in rows.scalars()}
    cons = tasks.get("conservative")
    std = tasks.get("standard")
    if cons is None or std is None:
        raise HTTPException(status_code=404, detail="敏感性对比组不存在或已不完整")

    async def _side(task: BacktestTask) -> SensitivitySide:
        report = (
            await db.execute(
                select(BacktestReport).where(BacktestReport.task_id == task.id)
            )
        ).scalar_one_or_none()
        return SensitivitySide(
            task_id=task.id,
            execution_mode=task.execution_mode,  # type: ignore[arg-type]
            status=task.status,  # type: ignore[arg-type]
            annual_return_percent=float(report.annual_return_percent) if report else None,
            max_drawdown_percent=float(report.max_drawdown_percent) if report else None,
            sharpe_ratio=float(report.sharpe_ratio) if report else None,
            total_return_percent=float(report.total_return_percent) if report else None,
        )

    c_side = await _side(cons)
    s_side = await _side(std)
    diff = None
    sensitive = False
    if c_side.annual_return_percent is not None and s_side.annual_return_percent is not None:
        diff = round(c_side.annual_return_percent - s_side.annual_return_percent, 6)
        sensitive = abs(diff) > _ANNUAL_DIFF_ALERT
    return SensitivityCompareOut(
        group_id=group_id, conservative=c_side, standard=s_side,
        annual_diff_percent=diff, sensitive=sensitive,
    )


# ---------------------------------------------------------------------------
# Overfit-guard analyses (Walk-Forward / Monte Carlo)
# NOTE: static /analyses paths must stay above the /{task_id} routes.
# ---------------------------------------------------------------------------

async def _get_owned_analysis(
    db: AsyncSession, analysis_id: uuid.UUID, current: dict
) -> BacktestAnalysis:
    row = await db.get(BacktestAnalysis, analysis_id)
    if row is None or str(row.user_id) != current["sub"]:
        raise HTTPException(status_code=404, detail="分析记录不存在")
    return row


@router.get("/analyses", response_model=AnalysisListResponse)
async def list_analyses(
    current: CurrentUser,
    db: DBSession,
    task_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=_PAGE_SIZE_MAX),
) -> AnalysisListResponse:
    filters = [BacktestAnalysis.user_id == _user_id(current)]
    if task_id is not None:
        filters.append(BacktestAnalysis.task_id == task_id)
    total = (
        await db.execute(
            select(func.count()).select_from(BacktestAnalysis).where(*filters)
        )
    ).scalar_one()
    rows = list(
        await db.scalars(
            select(BacktestAnalysis)
            .where(*filters)
            .order_by(BacktestAnalysis.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    # Compute-on-read refresh for running Walk-Forward / optimization analyses.
    refreshed: list[BacktestAnalysis] = []
    for row in rows:
        if row.status == "running":
            if row.kind == "walk_forward":
                updated = await aggregate_walk_forward(db, row)
                if updated is not None:
                    row = updated
            elif row.kind == "optimization":
                updated = await aggregate_optimization(db, row)
                if updated is not None:
                    row = updated
        refreshed.append(row)
    return AnalysisListResponse(
        items=[AnalysisOut.model_validate(r) for r in refreshed], total=total,
    )


@router.get("/analyses/{analysis_id}", response_model=AnalysisOut)
async def get_analysis(
    analysis_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> BacktestAnalysis:
    row = await _get_owned_analysis(db, analysis_id, current)
    if row.status == "running":
        if row.kind == "walk_forward":
            updated = await aggregate_walk_forward(db, row)
            if updated is not None:
                row = updated
        elif row.kind == "optimization":
            updated = await aggregate_optimization(db, row)
            if updated is not None:
                row = updated
    return row


@router.get("/slippage-calibration", response_model=SlippageCalibrationOut)
async def get_slippage_calibration(
    current: CurrentUser,
    db: DBSession,
    symbol: str = Query(..., min_length=1, max_length=32),
) -> SlippageCalibrationOut:
    """Real-fill slippage stats across the user's live runs on one symbol.

    Compute-on-read over live_trades (M4 真实滑点校准): the recommended
    value pre-fills the backtest form's slippage parameter so the backtest
    execution model converges toward the user's real environment.  Literal
    route — MUST stay registered before GET /{task_id}.
    """
    return SlippageCalibrationOut(
        **await calibration_for_symbol(db, _user_id(current), symbol.upper())
    )


@router.get("/queue/overview", response_model=QueueOverviewOut)
async def get_queue_overview(
    current: CurrentUser, db: DBSession
) -> QueueOverviewOut:
    """Global backtest queue depth (M18 任务队列监控).

    Running/pending are process-wide (the queue is shared infrastructure);
    ``oldest_pending_created_at`` is likewise global so users see real
    wait pressure, not just their own tasks.  Literal route — MUST stay
    registered before GET /{task_id}.
    """
    snap = get_backtest_queue().snapshot()
    oldest = (
        await db.execute(
            select(BacktestTask.created_at)
            .where(BacktestTask.status == "pending")
            .order_by(BacktestTask.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return QueueOverviewOut(
        running=snap["running"],
        pending_count=snap["pending_count"],
        oldest_pending_created_at=oldest,
    )


@router.post(
    "/{task_id}/walk-forward",
    response_model=AnalysisOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_walk_forward(
    task_id: uuid.UUID, payload: WalkForwardCreate, current: CurrentUser, db: DBSession
) -> BacktestAnalysis:
    """Spawn 2×folds child backtests (IS/OOS per fold) off a completed task."""
    src = await _get_owned_task(db, task_id, current)
    if src.status != "completed":
        raise HTTPException(
            status_code=409, detail="Walk-Forward 需要基于已完成的回测任务"
        )

    # Window guards before touching the queue.
    from app.services.backtest.analysis import split_walk_forward_windows

    fold_windows = split_walk_forward_windows(
        src.start_date, src.end_date, payload.folds, payload.mode
    )
    for is_s, is_e, oos_s, oos_e in fold_windows:
        for s, e in ((is_s, is_e), (oos_s, oos_e)):
            est = estimate_bars(s, e, src.timeframe, src.warmup_bars)
            if est > settings.BACKTEST_MAX_BARS:
                raise HTTPException(
                    status_code=422,
                    detail=f"折内窗口预估 K 线数 {est} 超过上限 {settings.BACKTEST_MAX_BARS}，"
                    "请缩短回测区间或减少折数",
                )

    analysis = new_analysis_row(
        user_id=src.user_id,
        task_id=src.id,
        kind="walk_forward",
        params={"folds": payload.folds, "mode": payload.mode, "children": []},
    )
    db.add(analysis)
    await db.flush()  # assign analysis.id for the child snapshot linkage

    children, mapping = build_walk_forward_children(src, analysis.id, payload.folds, payload.mode)
    db.add_all(children)
    await db.flush()
    for child, m in zip(children, mapping):
        m["task_id"] = str(child.id)
    analysis.params = {**analysis.params, "children": mapping}
    await db.commit()
    await db.refresh(analysis)

    # 审计行不挂在请求会话上：此处状态早已提交，传 db=db 的话没人再 commit，
    # 这行会随请求会话一起被回滚掉——审计静默丢失，事后查不到任何人做过什么。
    await audit.record(
        "backtest.walk_forward_create",
        actor=audit.AuditActor(
            actor_type="user",
            actor_id=_user_id(current),
            actor_label=str(current.get("username") or ""),
        ),
        target_type="backtest_analysis",
        target_id=str(analysis.id),
        params={"task_id": str(src.id), "folds": payload.folds, "mode": payload.mode},
    )
    if settings.BACKTEST_ENABLED:
        queue = get_backtest_queue()
        for child in children:
            queue.enqueue(str(child.id))
    return analysis


@router.post(
    "/{task_id}/monte-carlo",
    response_model=AnalysisOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_monte_carlo(
    task_id: uuid.UUID, payload: MonteCarloCreate, current: CurrentUser, db: DBSession
) -> BacktestAnalysis:
    """Bootstrap/shuffle the realised trade sequence (synchronous, seeded)."""
    task = await _get_owned_task(db, task_id, current)
    if task.status != "completed":
        raise HTTPException(
            status_code=409, detail="蒙特卡洛分析需要基于已完成的回测任务"
        )
    report = (
        await db.execute(select(BacktestReport).where(BacktestReport.task_id == task.id))
    ).scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=409, detail="回测报告尚未生成")

    profits = await load_source_trades(db, task.id)
    seed = payload.seed if payload.seed is not None else random.randrange(2**31)
    params = {
        "simulations": payload.simulations,
        "method": payload.method,
        "ruin_dd_percent": payload.ruin_dd_percent,
        "seed": seed,
    }
    try:
        result = run_monte_carlo(
            initial_deposit=float(task.initial_deposit),
            report=report,
            profits=profits,
            simulations=payload.simulations,
            method=payload.method,
            ruin_dd_percent=payload.ruin_dd_percent,
            seed=seed,
        )
    except AnalysisValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    analysis = new_analysis_row(
        user_id=task.user_id, task_id=task.id, kind="monte_carlo", params=params
    )
    analysis.result = result
    analysis.status = "completed"
    db.add(analysis)
    await db.commit()
    await db.refresh(analysis)

    # 审计行不挂在请求会话上：此处状态早已提交，传 db=db 的话没人再 commit，
    # 这行会随请求会话一起被回滚掉——审计静默丢失，事后查不到任何人做过什么。
    await audit.record(
        "backtest.monte_carlo_create",
        actor=audit.AuditActor(
            actor_type="user",
            actor_id=_user_id(current),
            actor_label=str(current.get("username") or ""),
        ),
        target_type="backtest_analysis",
        target_id=str(analysis.id),
        params={"task_id": str(task.id), **params},
    )
    return analysis


@router.post(
    "/{task_id}/optimize",
    response_model=AnalysisOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_optimization(
    task_id: uuid.UUID, payload: OptimizationCreate, current: CurrentUser, db: DBSession
) -> BacktestAnalysis:
    """Grid / random parameter search: one real child backtest per combo."""
    src = await _get_owned_task(db, task_id, current)
    if src.status != "completed":
        raise HTTPException(
            status_code=409, detail="参数优化需要基于已完成的回测任务"
        )

    # Same window as the source task — guard once before touching the queue.
    est = estimate_bars(src.start_date, src.end_date, src.timeframe, src.warmup_bars)
    if est > settings.BACKTEST_MAX_BARS:
        raise HTTPException(
            status_code=422,
            detail=f"预估 K 线数 {est} 超过上限 {settings.BACKTEST_MAX_BARS}",
        )

    space = [(p.name, p.values) for p in payload.space]
    if payload.method == "grid":
        combos = expand_grid(space)
    else:
        seed = payload.seed if payload.seed is not None else random.randrange(2**31)
        combos = sample_random(space, payload.samples, seed)

    analysis = new_analysis_row(
        user_id=src.user_id,
        task_id=src.id,
        kind="optimization",
        params={
            "method": payload.method,
            "objective": payload.objective,
            "samples": len(combos),
            "seed": payload.seed,
            "space": [{"name": n, "values": v} for n, v in space],
            "children": [],
        },
    )
    db.add(analysis)
    await db.flush()  # assign analysis.id for the child snapshot linkage

    children, mapping = build_optimization_children(src, analysis.id, combos)
    db.add_all(children)
    await db.flush()
    for child, m in zip(children, mapping):
        m["task_id"] = str(child.id)
    analysis.params = {**analysis.params, "children": mapping}
    await db.commit()
    await db.refresh(analysis)

    # 审计行不挂在请求会话上：此处状态早已提交，传 db=db 的话没人再 commit，
    # 这行会随请求会话一起被回滚掉——审计静默丢失，事后查不到任何人做过什么。
    await audit.record(
        "backtest.optimization_create",
        actor=audit.AuditActor(
            actor_type="user",
            actor_id=_user_id(current),
            actor_label=str(current.get("username") or ""),
        ),
        target_type="backtest_analysis",
        target_id=str(analysis.id),
        params={
            "task_id": str(src.id),
            "method": payload.method,
            "objective": payload.objective,
            "combos": len(combos),
        },
    )
    if settings.BACKTEST_ENABLED:
        queue = get_backtest_queue()
        for child in children:
            queue.enqueue(str(child.id))
    return analysis


# ---------------------------------------------------------------------------
# Portfolio backtest (multi-symbol, PRD Phase 3 P2)
# NOTE: static /portfolios paths must stay above the /{task_id} routes.
# ---------------------------------------------------------------------------


def _portfolio_out(
    row: PortfolioBacktest, name_map: dict[uuid.UUID, str]
) -> PortfolioOut:
    return PortfolioOut(
        id=row.id,
        strategy_id=row.strategy_id,
        strategy_name=name_map.get(row.strategy_id, ""),
        symbols=list(row.symbols),
        timeframe=row.timeframe,
        total_deposit=float(row.total_deposit),
        status=cast("Literal['running', 'completed', 'failed']", row.status),
        params=row.params,
        result=row.result,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _fetch_strategy_names(
    db: AsyncSession, strategy_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    ids = list({sid for sid in strategy_ids if sid is not None})
    if not ids:
        return {}
    rows = (
        await db.execute(select(Strategy.id, Strategy.name).where(Strategy.id.in_(ids)))
    ).all()
    return {r[0]: r[1] for r in rows}


@router.post(
    "/portfolios", response_model=PortfolioOut, status_code=status.HTTP_201_CREATED
)
async def create_portfolio(
    payload: PortfolioCreate, current: CurrentUser, db: DBSession
) -> PortfolioOut:
    """One strategy × N symbols: N real child tasks, equal-weight deposits."""
    user_id = _user_id(current)
    strategy = await db.get(Strategy, payload.strategy_id)
    if strategy is None or str(strategy.user_id) != current["sub"]:
        raise HTTPException(status_code=404, detail="策略不存在")
    if strategy.status == "archived":
        raise HTTPException(status_code=409, detail="已归档策略不可回测")
    if not strategy.source_code.strip():
        raise HTTPException(status_code=422, detail="策略代码为空，无法回测")

    est = estimate_bars(
        payload.start_date, payload.end_date, payload.timeframe, payload.warmup_bars
    )
    if est > settings.BACKTEST_MAX_BARS:
        raise HTTPException(
            status_code=422,
            detail=f"预估 K 线数 {est} 超过上限 {settings.BACKTEST_MAX_BARS}，请缩小区间或提高周期",
        )

    n = len(payload.symbols)
    per_deposit = round(payload.total_deposit / n, 2)
    if per_deposit <= 0:
        raise HTTPException(
            status_code=422, detail="总资金过小，均分后每品种资金不足 0.01"
        )

    portfolio = PortfolioBacktest(
        user_id=user_id,
        strategy_id=strategy.id,
        symbols=list(payload.symbols),
        timeframe=payload.timeframe,
        total_deposit=Decimal(str(round(per_deposit * n, 2))),
        params={
            "start": payload.start_date.isoformat(),
            "end": payload.end_date.isoformat(),
            "execution_mode": payload.execution_mode,
            "leverage": payload.leverage,
            "children": [],
        },
    )
    db.add(portfolio)
    await db.flush()  # assign portfolio.id for the child snapshot linkage

    children: list[BacktestTask] = []
    mapping: list[dict] = []
    for symbol in payload.symbols:
        per_payload = BacktestCreate(
            strategy_id=strategy.id,
            symbol=symbol,
            timeframe=payload.timeframe,
            start_date=payload.start_date,
            end_date=payload.end_date,
            initial_deposit=per_deposit,
            leverage=payload.leverage,
            execution_mode=payload.execution_mode,
            spread_mode=payload.spread_mode,
            spread_points=payload.spread_points,
            commission_per_lot=payload.commission_per_lot,
            slippage_points=payload.slippage_points,
            warmup_bars=payload.warmup_bars,
            parameters={},
            assumption_ack=True,
        )
        source = strategy.source_code
        snapshot = await _build_snapshot(per_payload, source, None)
        snapshot["portfolio_id"] = str(portfolio.id)
        snapshot["analysis_role"] = symbol
        task = _new_task(user_id, per_payload, source, snapshot,
                         payload.execution_mode, None)
        db.add(task)
        children.append(task)
        mapping.append({"symbol": symbol, "task_id": "", "deposit": per_deposit})

    await db.flush()
    for task, m in zip(children, mapping):
        m["task_id"] = str(task.id)
    portfolio.params = {**portfolio.params, "children": mapping}
    await db.commit()
    await db.refresh(portfolio)

    # 审计行不挂在请求会话上：此处状态早已提交，传 db=db 的话没人再 commit，
    # 这行会随请求会话一起被回滚掉——审计静默丢失，事后查不到任何人做过什么。
    await audit.record(
        "backtest.portfolio_create",
        actor=audit.AuditActor(
            actor_type="user",
            actor_id=user_id,
            actor_label=str(current.get("username") or ""),
        ),
        target_type="portfolio_backtest",
        target_id=str(portfolio.id),
        params={
            "strategy_id": str(strategy.id),
            "symbols": payload.symbols,
            "timeframe": payload.timeframe,
            "total_deposit": float(portfolio.total_deposit),
        },
    )
    if settings.BACKTEST_ENABLED:
        queue = get_backtest_queue()
        for task in children:
            queue.enqueue(str(task.id))

    names = await _fetch_strategy_names(db, [portfolio.strategy_id])
    return _portfolio_out(portfolio, names)


@router.get("/portfolios", response_model=PortfolioListResponse)
async def list_portfolios(
    current: CurrentUser,
    db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=_PAGE_SIZE_MAX),
) -> PortfolioListResponse:
    filters = [PortfolioBacktest.user_id == _user_id(current)]
    total = (
        await db.execute(
            select(func.count()).select_from(PortfolioBacktest).where(*filters)
        )
    ).scalar_one()
    rows = list(
        await db.scalars(
            select(PortfolioBacktest)
            .where(*filters)
            .order_by(PortfolioBacktest.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    # Compute-on-read refresh for running portfolios (same as analyses).
    refreshed: list[PortfolioBacktest] = []
    for row in rows:
        if row.status == "running":
            row = await aggregate_portfolio(db, row)
        refreshed.append(row)
    names = await _fetch_strategy_names(db, [r.strategy_id for r in refreshed])
    return PortfolioListResponse(
        items=[_portfolio_out(r, names) for r in refreshed],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total / page_size) if total else 0,
    )


@router.get("/portfolios/{portfolio_id}", response_model=PortfolioOut)
async def get_portfolio(
    portfolio_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> PortfolioOut:
    row = await db.get(PortfolioBacktest, portfolio_id)
    if row is None or str(row.user_id) != current["sub"]:
        raise HTTPException(status_code=404, detail="组合回测不存在")
    if row.status == "running":
        row = await aggregate_portfolio(db, row)
    names = await _fetch_strategy_names(db, [row.strategy_id])
    return _portfolio_out(row, names)


# ---------------------------------------------------------------------------
# Detail / report / trades / logs (/{task_id})
# ---------------------------------------------------------------------------

@router.get("/{task_id}", response_model=BacktestTaskDetailOut)
async def get_backtest(
    task_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> BacktestTask:
    return await _get_owned_task(db, task_id, current)


@router.get("/{task_id}/report")
async def get_report(
    task_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> dict:
    task = await _get_owned_task(db, task_id, current)
    report = (
        await db.execute(select(BacktestReport).where(BacktestReport.task_id == task.id))
    ).scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=409, detail="报告尚未生成（任务未完成）")
    return {
        "task": BacktestTaskDetailOut.model_validate(task).model_dump(mode="json"),
        "report": BacktestReportOut.model_validate(report).model_dump(mode="json"),
    }


@router.get("/{task_id}/trades", response_model=TradeListResponse)
async def list_trades(
    task_id: uuid.UUID,
    current: CurrentUser,
    db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=_PAGE_SIZE_MAX),
) -> TradeListResponse:
    task = await _get_owned_task(db, task_id, current)
    total = (
        await db.execute(
            select(func.count()).select_from(BacktestTrade).where(BacktestTrade.task_id == task.id)
        )
    ).scalar_one()
    result = await db.execute(
        select(BacktestTrade)
        .where(BacktestTrade.task_id == task.id)
        .order_by(BacktestTrade.open_time, BacktestTrade.ticket)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list(result.scalars())
    return TradeListResponse(
        items=[BacktestTradeOut.model_validate(r) for r in rows], total=total,
    )


@router.get("/{task_id}/trades.csv")
async def trades_csv(task_id: uuid.UUID, current: CurrentUser, db: DBSession):
    task = await _get_owned_task(db, task_id, current)
    rows = list(
        await db.scalars(
            select(BacktestTrade)
            .where(BacktestTrade.task_id == task.id)
            .order_by(BacktestTrade.open_time, BacktestTrade.ticket)
        )
    )

    def _fmt(v) -> str:
        if v is None:
            return ""
        if isinstance(v, datetime):
            return v.astimezone(UTC).isoformat()
        return str(v)

    async def _stream() -> AsyncGenerator[str, None]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        buf.write("\ufeff")  # UTF-8 BOM for Excel
        writer.writerow(_TRADE_CSV_HEADER)
        yield buf.getvalue()
        for t in rows:
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([
                t.ticket, t.direction, _fmt(t.volume), _fmt(t.open_time), _fmt(t.open_price),
                _fmt(t.base_price), _fmt(t.close_time), _fmt(t.close_price), _fmt(t.profit),
                _fmt(t.profit_percent), _fmt(t.swap), _fmt(t.commission), _fmt(t.spread_cost),
                _fmt(t.slippage_cost), t.comment, t.is_forced,
            ])
            yield buf.getvalue()

    filename = f"backtest_{task_id}.csv"
    return StreamingResponse(
        _stream(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{task_id}/logs", response_model=LogListResponse)
async def list_logs(
    task_id: uuid.UUID,
    current: CurrentUser,
    db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=200, ge=1, le=1000),
    kind: str | None = Query(default=None, description="log | signal | system"),
    level: str | None = Query(default=None, description="info | warn | error"),
) -> LogListResponse:
    task = await _get_owned_task(db, task_id, current)
    if kind is not None and kind not in ("log", "signal", "system"):
        raise HTTPException(status_code=422, detail="无效的 kind 筛选值")
    filters = [BacktestLog.task_id == task.id]
    if kind:
        filters.append(BacktestLog.kind == kind)
    if level:
        filters.append(BacktestLog.level == level)
    total = (
        await db.execute(select(func.count()).select_from(BacktestLog).where(*filters))
    ).scalar_one()
    result = await db.execute(
        select(BacktestLog)
        .where(*filters)
        .order_by(BacktestLog.seq)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    truncated = (task.config_snapshot or {}).get("log_dropped", 0) > 0
    rows = list(result.scalars())
    return LogListResponse(
        items=[BacktestLogOut.model_validate(r) for r in rows],
        total=total, truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Cancel / rerun
# ---------------------------------------------------------------------------

@router.post("/{task_id}/cancel", response_model=BacktestTaskOut)
async def cancel_backtest(
    task_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> BacktestTask:
    task = await _get_owned_task(db, task_id, current)
    if task.status in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail="任务已处于终态，无法取消")
    new_status = await get_backtest_queue().cancel(str(task_id))
    if new_status == "not_found":
        raise HTTPException(status_code=404, detail="回测任务不存在")
    await db.refresh(task)
    return task


@router.post("/{task_id}/rating", response_model=BacktestTaskOut)
async def rate_backtest(
    task_id: uuid.UUID, payload: BacktestRatingRequest, current: CurrentUser, db: DBSession
) -> BacktestTask:
    """打分 1-5 星; 传 null 清除评分。"""
    task = await _get_owned_task(db, task_id, current)
    task.rating = payload.rating
    await db.commit()
    await db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backtest(
    task_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> None:
    """删除回测任务记录 — 仅终态可删; 报告/成交/日志经外键 CASCADE 一并清理。"""
    task = await _get_owned_task(db, task_id, current)
    if task.status not in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail="运行中的回测请先取消，再删除")
    await db.delete(task)
    await db.commit()


@router.post(
    "/{task_id}/rerun", response_model=BacktestTaskOut, status_code=status.HTTP_201_CREATED
)
async def rerun_backtest(
    task_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> BacktestTask:
    src = await _get_owned_task(db, task_id, current)
    est = estimate_bars(src.start_date, src.end_date, src.timeframe, src.warmup_bars)
    if est > settings.BACKTEST_MAX_BARS:
        raise HTTPException(status_code=422, detail=f"预估 K 线数 {est} 超过上限")
    src_bars_file = Path(settings.BACKTEST_RUNTIME_DIR) / str(task_id) / "local_bars.json"
    if src.data_source == "local" and not src_bars_file.is_file():
        raise HTTPException(
            status_code=422, detail="本地数据文件已被清理，无法重跑；请改用服务器数据"
        )
    snap = dict(src.config_snapshot or {})
    snap["seed"] = random.randrange(2**31)  # fresh seed unless pinned in snapshot
    task = BacktestTask(
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
        parameters=src.parameters,
        data_source=src.data_source,
        config_snapshot=snap,
        group_id=src.group_id,
        created_by=_user_id(current),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    if src.data_source == "local":
        dst_dir = Path(settings.BACKTEST_RUNTIME_DIR) / str(task.id)
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_bars_file, dst_dir / "local_bars.json")
    if settings.BACKTEST_ENABLED:
        get_backtest_queue().enqueue(str(task.id))
    return task
