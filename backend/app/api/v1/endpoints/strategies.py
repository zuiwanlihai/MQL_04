"""
Strategy research endpoints (M3): CRUD, working copy, versions, templates,
static AST validation.

Route order matters: ``/templates`` and ``/validate`` are declared BEFORE
the ``/{strategy_id}`` routes so the path converter never swallows them.

``source_code`` (working copy) is only ever returned by the detail endpoint;
list responses exclude it.  Version freeze runs the AST validator first and
refuses to freeze error-level code (422 with the ValidationResult body).
"""

from __future__ import annotations

import math
import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.dependencies import CurrentUser, DBSession
from app.models import Strategy, StrategyTemplate, StrategyVersion
from app.schemas.strategy import (
    StrategyCreate,
    StrategyDetailOut,
    StrategyListResponse,
    StrategyOut,
    StrategySourceOut,
    StrategySourceUpdate,
    StrategyUpdate,
    StrategyVersionOut,
    TemplateListResponse,
    TemplateOut,
    VersionCreate,
    VersionListResponse,
)
from app.services.strategy_validator import validate_source

router = APIRouter()

_PAGE_SIZE_MAX = 100


async def _get_owned(
    db, strategy_id: uuid.UUID, current: dict
) -> Strategy:
    strategy = await db.get(Strategy, strategy_id)
    if strategy is None or str(strategy.user_id) != current["sub"]:
        raise HTTPException(status_code=404, detail="策略不存在")
    return strategy


# ---------------------------------------------------------------------------
# Templates (declared before /{strategy_id})
# ---------------------------------------------------------------------------

@router.get("/templates", response_model=TemplateListResponse)
async def list_templates(current: CurrentUser, db: DBSession) -> TemplateListResponse:
    result = await db.execute(
        select(StrategyTemplate).order_by(
            StrategyTemplate.sort_order, StrategyTemplate.name
        )
    )
    rows = list(result.scalars())
    return TemplateListResponse(items=rows, total=len(rows))


@router.get("/templates/{template_id}", response_model=TemplateOut)
async def get_template(
    template_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> TemplateOut:
    tpl = await db.get(StrategyTemplate, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return tpl


# ---------------------------------------------------------------------------
# Standalone validation (always 200 — findings live in the body)
# ---------------------------------------------------------------------------

@router.post("/validate")
async def validate(
    payload: StrategySourceUpdate, current: CurrentUser
) -> dict:
    return validate_source(payload.source_code).model_dump()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=StrategyOut, status_code=status.HTTP_201_CREATED)
async def create_strategy(
    payload: StrategyCreate, current: CurrentUser, db: DBSession
) -> Strategy:
    if payload.language != "python":
        raise HTTPException(status_code=422, detail="MVP 阶段仅支持 python 策略")

    source = ""
    if payload.template_id is not None:
        tpl = await db.get(StrategyTemplate, payload.template_id)
        if tpl is None:
            raise HTTPException(status_code=404, detail="模板不存在")
        source = tpl.source_code
    elif payload.source_code is not None:
        # 可视化构建器路径：以生成源码初始化工作副本
        source = payload.source_code

    user_id = uuid.UUID(current["sub"])
    strategy = Strategy(
        user_id=user_id,
        name=payload.name,
        description=payload.description,
        language=payload.language,
        direction=payload.direction,
        symbol_ids=payload.symbol_ids,
        timeframe=payload.timeframe,
        tags=payload.tags,
        source_code=source,
        builder_config=payload.builder_config,
        parameters=payload.parameters,
        created_by=user_id,
    )
    db.add(strategy)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="同名策略已存在") from None
    await db.refresh(strategy)
    return strategy


@router.get("", response_model=StrategyListResponse)
async def list_strategies(
    current: CurrentUser,
    db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=_PAGE_SIZE_MAX),
    status_filter: str | None = Query(default=None, alias="status"),
    q: str = Query(default="", max_length=80),
) -> StrategyListResponse:
    filters = [Strategy.user_id == uuid.UUID(current["sub"])]
    if status_filter:
        if status_filter not in ("draft", "active", "paused", "archived"):
            raise HTTPException(status_code=422, detail="无效的状态筛选值")
        filters.append(Strategy.status == status_filter)
    if q:
        like = f"%{q}%"
        filters.append(or_(Strategy.name.ilike(like), Strategy.description.ilike(like)))

    total = (
        await db.execute(select(func.count()).select_from(Strategy).where(*filters))
    ).scalar_one()
    result = await db.execute(
        select(Strategy)
        .where(*filters)
        .order_by(Strategy.updated_at.desc(), Strategy.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list(result.scalars())
    return StrategyListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(math.ceil(total / page_size), 1),
    )


@router.get("/{strategy_id}", response_model=StrategyDetailOut)
async def get_strategy(
    strategy_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> Strategy:
    return await _get_owned(db, strategy_id, current)


@router.patch("/{strategy_id}", response_model=StrategyOut)
async def update_strategy(
    strategy_id: uuid.UUID,
    payload: StrategyUpdate,
    current: CurrentUser,
    db: DBSession,
) -> Strategy:
    strategy = await _get_owned(db, strategy_id, current)
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(strategy, field, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="同名策略已存在") from None
    await db.refresh(strategy)
    return strategy


@router.delete("/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_strategy(
    strategy_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> None:
    strategy = await _get_owned(db, strategy_id, current)
    await db.delete(strategy)
    await db.commit()


# ---------------------------------------------------------------------------
# Working copy (optimistic locking)
# ---------------------------------------------------------------------------

@router.put("/{strategy_id}/source", response_model=StrategySourceOut)
async def save_source(
    strategy_id: uuid.UUID,
    payload: StrategySourceUpdate,
    current: CurrentUser,
    db: DBSession,
) -> StrategySourceOut:
    strategy = await _get_owned(db, strategy_id, current)
    if strategy.status == "archived":
        raise HTTPException(status_code=409, detail="已归档策略不可编辑")
    if payload.base_updated_at is not None:
        if strategy.updated_at is not None and payload.base_updated_at != strategy.updated_at:
            raise HTTPException(
                status_code=409, detail="策略已在其他窗口被修改，请刷新后重试"
            )
    strategy.source_code = payload.source_code
    await db.commit()
    await db.refresh(strategy)
    return StrategySourceOut(
        source_code=strategy.source_code,
        updated_at=strategy.updated_at,
        validation=validate_source(strategy.source_code),
    )


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

async def _latest_version(db, strategy_id: uuid.UUID) -> StrategyVersion | None:
    result = await db.execute(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy_id)
        .order_by(StrategyVersion.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.get("/{strategy_id}/versions", response_model=VersionListResponse)
async def list_versions(
    strategy_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> VersionListResponse:
    strategy = await _get_owned(db, strategy_id, current)
    result = await db.execute(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy.id)
        .order_by(StrategyVersion.version.desc())
    )
    rows = list(result.scalars())
    return VersionListResponse(items=rows, total=len(rows))


@router.post(
    "/{strategy_id}/versions",
    response_model=StrategyVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def freeze_version(
    strategy_id: uuid.UUID,
    payload: VersionCreate,
    current: CurrentUser,
    db: DBSession,
) -> StrategyVersion:
    strategy = await _get_owned(db, strategy_id, current)
    report = validate_source(strategy.source_code)
    if not report.ok:
        raise HTTPException(status_code=422, detail=report.model_dump())

    latest = await _latest_version(db, strategy.id)
    if latest is not None and latest.source_code == strategy.source_code:
        raise HTTPException(
            status_code=409, detail=f"代码与 v{latest.version} 无变化，无需冻结"
        )
    version = StrategyVersion(
        strategy_id=strategy.id,
        version=(latest.version + 1) if latest else 1,
        source_code=strategy.source_code,
        changelog=payload.changelog,
        created_by=uuid.UUID(current["sub"]),
    )
    db.add(version)
    strategy.current_version = version.version
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="版本号冲突，请重试") from None
    await db.refresh(version)
    return version


@router.post(
    "/{strategy_id}/versions/{version}/rollback",
    response_model=StrategyVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def rollback_version(
    strategy_id: uuid.UUID,
    version: int,
    current: CurrentUser,
    db: DBSession,
) -> StrategyVersion:
    strategy = await _get_owned(db, strategy_id, current)
    result = await db.execute(
        select(StrategyVersion).where(
            StrategyVersion.strategy_id == strategy.id,
            StrategyVersion.version == version,
        )
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    if strategy.current_version == version:
        raise HTTPException(status_code=409, detail="已是当前生效版本")

    latest = await _latest_version(db, strategy.id)
    frozen = StrategyVersion(
        strategy_id=strategy.id,
        version=(latest.version + 1) if latest else 1,
        source_code=target.source_code,
        changelog=f"回滚至 v{version}",
        created_by=uuid.UUID(current["sub"]),
    )
    db.add(frozen)
    strategy.current_version = frozen.version
    strategy.source_code = target.source_code
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="版本号冲突，请重试") from None
    await db.refresh(frozen)
    return frozen
