"""Paper trading center endpoints (M5).

All queries are user-scoped (``user_id == sub``); cross-owner access returns
404, never 403, to avoid run-id enumeration — same contract as backtests.
Starting a run freezes a ``config_snapshot`` (tier assumption text, seed,
versions); the manager later enriches it with symbol_info / spread snapshot.

Control endpoints (pause / resume / stop / reset) delegate to the manager
singleton; status transitions are CAS'd there, so a double-click is a no-op
rather than a race.
"""

from __future__ import annotations

import json
import math
import random
import sys
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import CurrentUser, DBSession
from app.models import (
    MT5Account,
    PaperLog,
    PaperRun,
    PaperTrade,
    Strategy,
    StrategyVersion,
)
from app.sandbox.payload import describe_execution_mode
from app.schemas.paper import (
    LogListResponse,
    PaperCreate,
    PaperListResponse,
    PaperLogOut,
    PaperRunDetailOut,
    PaperRunOut,
    PaperStopRequest,
    PaperTradeOut,
    TradeListResponse,
)
from app.services.gateway_route import probe_active_account_type
from app.services.paper.manager import get_paper_manager

router = APIRouter()

_PAGE_SIZE_MAX = 100


def _user_id(current: dict) -> uuid.UUID:
    return uuid.UUID(current["sub"])


def _read_strategy_state(runtime_dir: Path, run_id: str) -> dict | None:
    """读取子进程持久化的 ctx.storage（storage.json），best-effort。

    网格等模板把锚定价存在 ctx.storage，引擎每根 K 线后落盘到运行目录；
    停止/失败清目录后自然回退 None，前端按无网格状态降级。
    """
    path = runtime_dir / run_id / "storage.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _dependency_versions() -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version

    out: dict[str, str] = {"python": sys.version.split()[0], "platform": settings.APP_VERSION}
    for pkg in ("numpy", "pandas", "scipy"):
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:
            out[pkg] = "n/a"
    return out


def _source_hash(source: str) -> str:
    import hashlib

    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def _dec(v: float | None, places: int = 4):
    from decimal import Decimal

    return None if v is None else Decimal(str(round(float(v), places)))


async def _get_owned_run(db: AsyncSession, run_id: uuid.UUID, current: dict) -> PaperRun:
    run = await db.get(PaperRun, run_id)
    if run is None or str(run.user_id) != current["sub"]:
        raise HTTPException(status_code=404, detail="虚拟下单任务不存在")
    return run


# ---------------------------------------------------------------------------
# Create (POST /paper)
# ---------------------------------------------------------------------------

@router.post("", response_model=PaperRunOut, status_code=status.HTTP_201_CREATED)
async def create_paper_run(
    payload: PaperCreate, current: CurrentUser, db: DBSession
) -> PaperRun:
    user_id = _user_id(current)
    strategy = await db.get(Strategy, payload.strategy_id)
    if strategy is None or str(strategy.user_id) != current["sub"]:
        raise HTTPException(status_code=404, detail="策略不存在")
    if strategy.status == "archived":
        raise HTTPException(status_code=409, detail="已归档策略不可启动虚拟下单")

    # M12: the run must bind an MT5 account it can route its gateway calls to.
    acct = await db.get(MT5Account, payload.account_id)
    if acct is None or (str(acct.user_id) != current["sub"] and acct.user_id is not None):
        raise HTTPException(status_code=404, detail="MT5 账户不存在")

    # 虚拟下单/实盘互斥（后端双保险）：虚拟下单仅限模拟账户。
    # ① 绑定行类型；② 节点当前登录实况（探活不可达时跳过，由 ① 兜底）。
    if acct.account_type != "demo":
        raise HTTPException(
            status_code=409,
            detail="虚拟下单仅支持模拟账户，当前绑定的是实盘账户；"
            "请先在总览页切换登录到模拟账户",
        )
    if await probe_active_account_type(db, acct.gateway_node_id) == "real":
        raise HTTPException(
            status_code=409,
            detail="虚拟下单仅支持模拟账户，当前登录的是实盘账户；"
            "请先在总览页切换登录到模拟账户",
        )

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
        raise HTTPException(status_code=422, detail="策略代码为空，无法启动虚拟下单")

    # Concurrency cap: active = running + paused + pending.
    active = (
        await db.execute(
            select(func.count()).select_from(PaperRun).where(
                PaperRun.user_id == user_id,
                PaperRun.status.in_(["pending", "running", "paused"]),
            )
        )
    ).scalar_one()
    if active >= settings.PAPER_MAX_CONCURRENT:
        raise HTTPException(
            status_code=409,
            detail=f"同时运行的虚拟下单数量已达上限（{settings.PAPER_MAX_CONCURRENT}）",
        )

    snapshot = {
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
        "account": {"id": str(acct.id), "login": acct.login,
                    "server": acct.server},
    }
    run = PaperRun(
        user_id=user_id,
        strategy_id=payload.strategy_id,
        account_id=payload.account_id,
        source_code=source,
        strategy_version=version,
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        execution_mode=payload.execution_mode,
        initial_deposit=_dec(payload.initial_deposit),
        leverage=payload.leverage,
        spread_mode=payload.spread_mode,
        spread_points=_dec(payload.spread_points, 2),
        commission_per_lot=_dec(payload.commission_per_lot),
        slippage_points=_dec(payload.slippage_points, 2),
        warmup_bars=payload.warmup_bars,
        parameters=payload.parameters,
        config_snapshot=snapshot,
        balance=_dec(payload.initial_deposit),
        equity=_dec(payload.initial_deposit),
        margin_free=_dec(payload.initial_deposit),
        created_by=user_id,
    )
    db.add(run)
    try:
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"虚拟下单创建失败：{exc}") from None
    await db.refresh(run)
    if settings.PAPER_ENABLED:
        # Fire-and-forget: the manager CAS-claims the row; the API stays 201.
        get_paper_manager().spawn_start(str(run.id))
    return run


# ---------------------------------------------------------------------------
# List (GET /paper)
# ---------------------------------------------------------------------------

@router.get("", response_model=PaperListResponse)
async def list_paper_runs(
    current: CurrentUser,
    db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=_PAGE_SIZE_MAX),
    strategy_id: uuid.UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
) -> PaperListResponse:
    filters = [PaperRun.user_id == _user_id(current)]
    if strategy_id is not None:
        filters.append(PaperRun.strategy_id == strategy_id)
    if status_filter:
        if status_filter not in ("pending", "running", "paused", "stopping",
                                 "stopped", "failed"):
            raise HTTPException(status_code=422, detail="无效的状态筛选值")
        filters.append(PaperRun.status == status_filter)

    total = (
        await db.execute(select(func.count()).select_from(PaperRun).where(*filters))
    ).scalar_one()
    result = await db.execute(
        select(PaperRun)
        .where(*filters)
        .order_by(PaperRun.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list(result.scalars())
    return PaperListResponse(
        items=[PaperRunOut.model_validate(r) for r in items],
        total=total, page=page, page_size=page_size,
        total_pages=max(math.ceil(total / page_size), 1),
    )


# ---------------------------------------------------------------------------
# Detail / trades / logs (/{run_id})
# ---------------------------------------------------------------------------

@router.get("/{run_id}", response_model=PaperRunDetailOut)
async def get_paper_run(
    run_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> PaperRunDetailOut:
    run = await _get_owned_run(db, run_id, current)
    detail = PaperRunDetailOut.model_validate(run)
    detail.strategy_state = _read_strategy_state(
        Path(settings.PAPER_RUNTIME_DIR), str(run_id)
    )
    return detail


@router.get("/{run_id}/trades", response_model=TradeListResponse)
async def list_trades(
    run_id: uuid.UUID,
    current: CurrentUser,
    db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=_PAGE_SIZE_MAX),
) -> TradeListResponse:
    run = await _get_owned_run(db, run_id, current)
    total = (
        await db.execute(
            select(func.count()).select_from(PaperTrade).where(PaperTrade.run_id == run.id)
        )
    ).scalar_one()
    result = await db.execute(
        select(PaperTrade)
        .where(PaperTrade.run_id == run.id)
        .order_by(PaperTrade.open_time, PaperTrade.ticket)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return TradeListResponse(
        items=[PaperTradeOut.model_validate(r) for r in result.scalars()], total=total
    )


@router.get("/{run_id}/logs", response_model=LogListResponse)
async def list_logs(
    run_id: uuid.UUID,
    current: CurrentUser,
    db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=200, ge=1, le=1000),
    kind: str | None = Query(default=None, description="log | signal | system"),
    level: str | None = Query(default=None, description="info | warn | error"),
) -> LogListResponse:
    run = await _get_owned_run(db, run_id, current)
    if kind is not None and kind not in ("log", "signal", "system"):
        raise HTTPException(status_code=422, detail="无效的 kind 筛选值")
    filters = [PaperLog.run_id == run.id]
    if kind:
        filters.append(PaperLog.kind == kind)
    if level:
        filters.append(PaperLog.level == level)
    total = (
        await db.execute(select(func.count()).select_from(PaperLog).where(*filters))
    ).scalar_one()
    result = await db.execute(
        select(PaperLog)
        .where(*filters)
        .order_by(PaperLog.seq.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return LogListResponse(
        items=[PaperLogOut.model_validate(r) for r in result.scalars()], total=total
    )


# ---------------------------------------------------------------------------
# Control: pause / resume / stop / reset
# ---------------------------------------------------------------------------

@router.post("/{run_id}/pause", response_model=PaperRunOut)
async def pause_paper_run(
    run_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> PaperRun:
    run = await _get_owned_run(db, run_id, current)
    if run.status not in ("running", "paused"):
        raise HTTPException(status_code=409, detail="只有运行中/已暂停的虚拟下单可以暂停")
    new_status = await get_paper_manager().pause(str(run_id))
    if new_status == "not_found":
        raise HTTPException(status_code=404, detail="虚拟下单任务不存在")
    await db.refresh(run)
    return run


@router.post("/{run_id}/resume", response_model=PaperRunOut)
async def resume_paper_run(
    run_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> PaperRun:
    run = await _get_owned_run(db, run_id, current)
    if run.status != "paused":
        raise HTTPException(status_code=409, detail="只有已暂停的虚拟下单可以恢复")
    new_status = await get_paper_manager().resume(str(run_id))
    if new_status == "not_found":
        raise HTTPException(status_code=404, detail="虚拟下单任务不存在")
    await db.refresh(run)
    return run


@router.post("/{run_id}/reset-anchor", response_model=PaperRunOut)
async def reset_paper_anchor(
    run_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> PaperRun:
    """网格策略专用：把锚定价重置为当前最新买价（storage["anchor"]，运行时生效）。"""
    run = await _get_owned_run(db, run_id, current)
    if run.status not in ("running", "paused"):
        raise HTTPException(status_code=409, detail="只有运行中/已暂停的虚拟下单可以重置锚点")
    result = await get_paper_manager().reset_anchor(str(run_id))
    if result != "ok":
        raise HTTPException(
            status_code=409, detail="策略进程未就绪（启动回放中或已退出），无法重置锚点"
        )
    await db.refresh(run)
    return run


@router.post("/{run_id}/stop", response_model=PaperRunOut)
async def stop_paper_run(
    run_id: uuid.UUID, payload: PaperStopRequest, current: CurrentUser, db: DBSession
) -> PaperRun:
    run = await _get_owned_run(db, run_id, current)
    if run.status in ("stopped", "failed"):
        raise HTTPException(status_code=409, detail="虚拟下单已处于终态，无法停止")
    new_status = await get_paper_manager().stop(str(run_id), payload.close_positions)
    if new_status == "not_found":
        raise HTTPException(status_code=404, detail="虚拟下单任务不存在")
    await db.refresh(run)
    return run


@router.post("/{run_id}/reset", response_model=PaperRunOut)
async def reset_paper_run(
    run_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> PaperRun:
    run = await _get_owned_run(db, run_id, current)
    if run.status in ("pending", "running", "paused"):
        raise HTTPException(
            status_code=409, detail="请先停止虚拟下单，再重置（清空成交/日志/状态）"
        )
    new_status = await get_paper_manager().reset(str(run_id))
    if new_status == "not_found":
        raise HTTPException(status_code=404, detail="虚拟下单任务不存在")
    await db.refresh(run)
    return run


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_paper_run(
    run_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> None:
    """删除虚拟下单记录 — 仅终态（已停止/失败）可删除。

    成交与日志经外键 CASCADE 一并清理；risk 关联表为 SET NULL，不受影响。
    """
    run = await _get_owned_run(db, run_id, current)
    if run.status not in ("stopped", "failed"):
        raise HTTPException(
            status_code=409, detail="运行中/已暂停的虚拟下单请先停止，再删除"
        )
    await db.delete(run)
    await db.commit()
