"""Tick data management endpoints (M-Tick, PRD 数据中心 P1).

Sync jobs are user-owned; the stored tick pool itself is shared read-only
data (single-tenant deployment).  Literal GET routes are registered before
the ``/{symbol}`` catch-all so they can never be swallowed by it.
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.config import settings
from app.dependencies import CurrentUser, DBSession
from app.models import TickData, TickSyncJob
from app.schemas.tick import (
    TickCoverageResponse,
    TickJobListResponse,
    TickListResponse,
    TickPurgeResponse,
    TickRowOut,
    TickSyncJobOut,
    TickSyncRequest,
)
from app.services import audit, tick_sync

router = APIRouter()

DAY_SECONDS = 86_400


def _user_id(current: CurrentUser) -> uuid_mod.UUID:
    return uuid_mod.UUID(str(current["sub"]))


@router.post("/sync", response_model=TickSyncJobOut, status_code=201)
async def request_sync(
    body: TickSyncRequest,
    current: CurrentUser,
    db: DBSession,
) -> TickSyncJobOut:
    """Queue a tick pull for [start, end) — serially executed in-process."""
    symbol = body.symbol.strip().upper()
    if body.end <= body.start:
        raise HTTPException(status_code=422, detail="结束时间必须晚于起始时间")
    max_span = settings.TICK_SYNC_MAX_SPAN_DAYS * DAY_SECONDS
    if body.end - body.start > max_span:
        raise HTTPException(
            status_code=422,
            detail=f"单次同步范围不能超过 {settings.TICK_SYNC_MAX_SPAN_DAYS} 天",
        )
    if not settings.TICK_SYNC_ENABLED:
        raise HTTPException(status_code=503, detail="Tick 同步功能未启用")

    job = TickSyncJob(
        user_id=_user_id(current),
        symbol=symbol,
        start_time=datetime.fromtimestamp(body.start, tz=UTC),
        end_time=datetime.fromtimestamp(body.end, tz=UTC),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # 审计行不挂在请求会话上：此处状态早已提交，传 db=db 的话没人再 commit，
    # 这行会随请求会话一起被回滚掉——审计静默丢失，事后查不到任何人做过什么。
    await audit.record(
        "tick.sync_request",
        actor=audit.AuditActor(
            actor_type="user",
            actor_id=_user_id(current),
            actor_label=str(current.get("username") or ""),
        ),
        target_type="symbol",
        target_id=symbol,
        params={"start": body.start, "end": body.end},
    )
    queue = tick_sync.get_tick_sync_queue()
    queue.enqueue(str(job.id))
    return TickSyncJobOut.model_validate(_job_attrs(job))


@router.get("/jobs", response_model=TickJobListResponse)
async def list_jobs(
    current: CurrentUser,
    db: DBSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> TickJobListResponse:
    """The requesting user's sync jobs, newest first."""
    rows = (await db.execute(
        select(TickSyncJob)
        .where(TickSyncJob.user_id == _user_id(current))
        .order_by(TickSyncJob.created_at.desc())
        .limit(limit)
    )).scalars().all()
    items = [TickSyncJobOut.model_validate(_job_attrs(j)) for j in rows]
    return TickJobListResponse(items=items, total=len(items))


@router.get("/coverage", response_model=TickCoverageResponse)
async def get_coverage(
    current: CurrentUser,
) -> TickCoverageResponse:
    """Stored per-symbol tick coverage (row count + min/max time)."""
    items = await tick_sync.coverage()
    return TickCoverageResponse(items=items)  # type: ignore[arg-type]


@router.get("/{symbol}", response_model=TickListResponse)
async def list_ticks(
    symbol: str,
    current: CurrentUser,
    db: DBSession,
    start: int = Query(default=0, ge=0, description="起始时间（epoch 秒，含）"),
    end: int = Query(default=0, ge=0, description="结束时间（epoch 秒，不含）；0=不限"),
    limit: int = Query(default=500, ge=1, le=2000),
) -> TickListResponse:
    """Stored ticks ascending by time_msc."""
    sym = symbol.strip().upper()
    stmt = select(TickData).where(TickData.symbol == sym)
    if start > 0:
        stmt = stmt.where(TickData.time_msc >= start * 1000)
    if end > 0:
        stmt = stmt.where(TickData.time_msc < end * 1000)
    rows = (await db.execute(stmt.order_by(TickData.time_msc.asc()).limit(limit))).scalars().all()
    items = [
        TickRowOut(
            time_msc=r.time_msc, bid=r.bid, ask=r.ask,
            last=r.last, volume=r.volume, flags=r.flags,
        )
        for r in rows
    ]
    return TickListResponse(items=items, total=len(items))


@router.delete("/{symbol}", response_model=TickPurgeResponse)
async def purge_symbol(
    symbol: str,
    current: CurrentUser,
    db: DBSession,
) -> TickPurgeResponse:
    """Delete every stored tick for the symbol (audit-logged)."""
    sym = symbol.strip().upper()
    deleted = await tick_sync.TickSyncQueue.purge_symbol(sym)
    # 审计行不挂在请求会话上：此处状态早已提交，传 db=db 的话没人再 commit，
    # 这行会随请求会话一起被回滚掉——审计静默丢失，事后查不到任何人做过什么。
    await audit.record(
        "tick.purge",
        actor=audit.AuditActor(
            actor_type="user",
            actor_id=_user_id(current),
            actor_label=str(current.get("username") or ""),
        ),
        target_type="symbol",
        target_id=sym,
        params={"deleted": deleted},
    )
    return TickPurgeResponse(symbol=sym, deleted=deleted)


def _job_attrs(job: TickSyncJob) -> dict:
    """Row → schema input dict (UUID PK / datetimes kept as-is for pydantic)."""
    return {
        "id": str(job.id),
        "symbol": job.symbol,
        "start_time": job.start_time,
        "end_time": job.end_time,
        "status": job.status,
        "tick_count": job.tick_count,
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }
