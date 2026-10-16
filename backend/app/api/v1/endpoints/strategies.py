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

import hashlib
import math
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.dependencies import CurrentUser, DBSession
from app.models import Strategy, StrategyTemplate, StrategyVersion
from app.schemas.strategy import (
    StrategyBuilderSave,
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
    TemplateSummaryOut,
    VersionCreate,
    VersionListResponse,
)
from app.services.builder_validator import validate_builder_config
from app.services.strategy_validator import validate_source

router = APIRouter()

_PAGE_SIZE_MAX = 100


def _source_hash(source: str) -> str:
    """SHA-256 hex of source_code — used for divergence detection."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


async def _check_optimistic(
    strategy: Strategy, base_updated_at: datetime | None,
) -> None:
    if base_updated_at is not None:
        if strategy.updated_at is not None and base_updated_at != strategy.updated_at:
            raise HTTPException(
                status_code=409, detail="策略已在其他窗口被修改，请刷新后重试"
            )


def _raise_bad_builder(errors: list[dict]) -> None:
    """把 DSL 校验错误列表转为 422（前端友好，含 targetId 定位）。"""
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"type": "builder_config", "errors": errors},
        )


def _raise_bad_source_if_error(source: str) -> None:
    """AST 校验：error 级直接拒绝保存；warning 级放行。"""
    report = validate_source(source)
    if not report.ok:
        raise HTTPException(
            status_code=422,
            detail={
                "type": "source_code",
                "issues": [i.model_dump() for i in report.issues],
            },
        )


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
    return TemplateListResponse(
        items=[TemplateSummaryOut.model_validate(r) for r in rows], total=len(rows)
    )


@router.get("/templates/{template_id}", response_model=TemplateOut)
async def get_template(
    template_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> TemplateOut:
    tpl = await db.get(StrategyTemplate, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return TemplateOut.model_validate(tpl)


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

    # P1：服务端 DSL + AST 校验（防御性校验，前端已做一轮）
    if payload.builder_config is not None:
        _raise_bad_builder(validate_builder_config(payload.builder_config))
    if source:
        _raise_bad_source_if_error(source)

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
        items=[StrategyOut.model_validate(r) for r in items],
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
    if strategy.status == "archived":
        raise HTTPException(status_code=409, detail="已归档策略不可编辑")

    # 乐观锁：base_updated_at 不写入 DB，仅做并发校验
    base = payload.base_updated_at
    data = payload.model_dump(exclude_unset=True, exclude={"base_updated_at"})
    await _check_optimistic(strategy, base)

    # P1：如果本次显式传入 builder_config（且非 None 清除）→ 跑 DSL 校验
    bc_in_data = "builder_config" in data
    if bc_in_data and data["builder_config"] is not None:
        _raise_bad_builder(validate_builder_config(data["builder_config"]))
    # 同样：如果显式传入 direction，要和现有 builder_config 的规则方向不冲突
    if "direction" in data and strategy.builder_config is not None:
        _raise_bad_builder(
            validate_builder_config({**strategy.builder_config, "direction": data["direction"]})
        )

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
    await _check_optimistic(strategy, payload.base_updated_at)

    strategy.source_code = payload.source_code
    strategy.source_code_hash = _source_hash(payload.source_code)
    await db.commit()
    await db.refresh(strategy)
    return StrategySourceOut(
        source_code=strategy.source_code,
        updated_at=strategy.updated_at if strategy.updated_at is not None else datetime.now(),
        validation=validate_source(strategy.source_code),
    )


# ---------------------------------------------------------------------------
# Atomic builder save (replaces the split saveSource + update flow)
# ---------------------------------------------------------------------------

@router.put("/{strategy_id}/builder-save", response_model=StrategyDetailOut)
async def builder_save(
    strategy_id: uuid.UUID,
    payload: StrategyBuilderSave,
    current: CurrentUser,
    db: DBSession,
) -> Strategy:
    """Single-transaction save: source_code + builder_config + metadata.

    替换前端 BuilderPage 原来两次独立调用（saveSource 再 update）的非原子保存流。
    后端会计算 source_code 的 SHA-256 存入 source_code_hash，供下次进入构建器时
    判断源码是否被手动修改过（脱离检测）。
    """
    strategy = await _get_owned(db, strategy_id, current)
    if strategy.status == "archived":
        raise HTTPException(status_code=409, detail="已归档策略不可编辑")
    await _check_optimistic(strategy, payload.base_updated_at)

    # P1：服务端 DSL 校验
    _raise_bad_builder(validate_builder_config(payload.builder_config))
    # P1：服务端 AST 校验（error 级直接拒绝）
    _raise_bad_source_if_error(payload.source_code)

    # 同时更新源码 + builder_config + 元数据 + hash —— 单次事务原子写入
    strategy.source_code = payload.source_code
    strategy.source_code_hash = _source_hash(payload.source_code)
    strategy.builder_config = payload.builder_config
    if payload.direction is not None:
        strategy.direction = payload.direction
    if payload.symbol_ids is not None:
        strategy.symbol_ids = payload.symbol_ids
    if payload.timeframe is not None:
        strategy.timeframe = payload.timeframe

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="同名策略已存在") from None
    await db.refresh(strategy)
    return strategy


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
    return VersionListResponse(
        items=[StrategyVersionOut.model_validate(r) for r in rows], total=len(rows)
    )


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

    # 回滚前做 AST 校验（安全网：旧版本源码不能是 error 级问题）
    _raise_bad_source_if_error(target.source_code)

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
    strategy.source_code_hash = _source_hash(target.source_code)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="版本号冲突，请重试") from None
    await db.refresh(frozen)
    return frozen
