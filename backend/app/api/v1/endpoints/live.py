"""Live trading center endpoints (M6).

Deployment is gated by the four admission gates (no bypass path); every
money/permission-touching action writes an audit row.  Ownership model matches
paper/backtests: cross-user access → 404, never 403.
"""

from __future__ import annotations

import json
import math
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import CurrentUser, DBSession
from app.models import (
    AlertEvent,
    GatewayNode,
    LiveLog,
    LiveRun,
    LiveTrade,
    MT5Account,
    RiskEvent,
    RiskRule,
    Strategy,
    StrategyVersion,
    User,
)
from app.sandbox.payload import describe_execution_mode
from app.schemas.live import (
    AdmissionCheckRequest,
    AdmissionOut,
    AlertListResponse,
    AlertOut,
    GateOut,
    KillSwitchRequest,
    LiveCreate,
    LiveListResponse,
    LiveLogListResponse,
    LiveRunDetailOut,
    LiveRunOut,
    LiveStopRequest,
    LiveTradeListResponse,
    ReAdmitRequest,
    RiskEventListResponse,
    RiskRuleCreate,
    RiskRuleOut,
    RiskRuleUpdate,
    UnfreezeRequest,
)
from app.schemas.slippage import DeviationReportOut
from app.services.audit import AuditActor
from app.services.audit import record as audit_record
from app.services.gateway_route import probe_active_account_type
from app.services.live.admission import evaluate_admission
from app.services.live.manager import get_live_manager
from app.services.slippage import deviation_report

router = APIRouter()

_PAGE_SIZE_MAX = 100
_RUN_STATUSES = ("pending", "running", "paused", "stopping", "stopped", "failed")


def _user_id(current: dict) -> uuid.UUID:
    return uuid.UUID(current["sub"])


def _actor(current: dict, request: Request | None = None) -> AuditActor:
    ip = request.client.host if request is not None and request.client else ""
    return AuditActor(actor_type="user", actor_id=_user_id(current),
                      actor_label=str(current.get("email") or current["sub"])[:120],
                      source_ip=ip or "")


def _dependency_versions() -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version

    out: dict[str, str] = {"python": sys.version.split()[0],
                           "platform": settings.APP_VERSION}
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


def _read_strategy_state(run_id: str) -> dict | None:
    """读取子进程持久化的 ctx.storage（storage.json），best-effort。

    网格等模板把锚定价存在 ctx.storage，引擎每根 K 线后落盘到运行目录；
    失败路径清目录后自然回退 None，前端按无网格状态降级。
    """
    path = Path(settings.LIVE_RUNTIME_DIR) / run_id / "storage.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


# ── 风控规则自动备注：新建时未填写则自动生成可读摘要 ─────────────

_RULE_TYPE_LABELS_ZH: dict[str, str] = {
    "daily_max_loss": "单日最大亏损（金额）",
    "daily_max_loss_pct": "单日最大亏损（百分比）",
    "max_drawdown": "最大总回撤（百分比）",
    "max_lot_per_order": "单笔最大手数",
    "max_open_positions": "最大持仓数",
    "max_exposure": "最大同向敞口",
    "max_orders_per_minute": "每分钟下单频率",
    "consecutive_loss_cooldown": "连续亏损暂停",
    "order_rate_window": "时间窗口限频",
    "manual_lot_cap": "平台上限（单笔手数）",
    "reconcile_frozen": "对账不一致冻结",
}

_RULE_TYPE_UNITS_ZH: dict[str, str] = {
    "daily_max_loss": "USD",
    "daily_max_loss_pct": "%",
    "max_drawdown": "%",
    "max_lot_per_order": "手",
    "max_open_positions": "单",
    "max_exposure": "手",
    "max_orders_per_minute": "次",
    "consecutive_loss_cooldown": "笔",
    "order_rate_window": "次",
}

_RULE_ACTION_LABELS_ZH: dict[str, str] = {
    "reject": "拒绝下单",
    "halt_open_today": "当日停止开仓",
    "close_all_and_stop": "全平仓+停止策略",
}

_RULE_APPLY_TO_LABELS_ZH: dict[str, str] = {
    "ai": "AI",
    "manual": "手动",
    "both": "AI+手动",
}


def _fmt_rule_num(v) -> str:
    return f"{float(v):g}"


def _fmt_rule_minutes(m: int) -> str:
    h, mm = divmod(m, 60)
    if h and mm:
        return f"{h} 小时 {mm} 分钟"
    if h:
        return f"{h} 小时"
    return f"{mm} 分钟"


def auto_rule_note(
    rule_type: str, threshold, action: str, apply_to: str = "both",
    *, cooldown_minutes=None, window_minutes=None,
) -> str:
    """新建风控规则未填写备注时，自动生成人类可读摘要（≤200 字）。"""
    text = (
        f"{_RULE_TYPE_LABELS_ZH.get(rule_type, rule_type)} "
        f"{_fmt_rule_num(threshold)}{_RULE_TYPE_UNITS_ZH.get(rule_type, '')}"
    )
    if rule_type == "consecutive_loss_cooldown" and cooldown_minutes:
        text += f"（休 {_fmt_rule_minutes(int(cooldown_minutes))}）"
    if rule_type == "order_rate_window" and window_minutes:
        text += f"（每 {int(window_minutes)} 分钟）"
    text += (
        f" · {_RULE_APPLY_TO_LABELS_ZH.get(apply_to, apply_to)}"
        f" · 触发后{_RULE_ACTION_LABELS_ZH.get(action, action)}"
    )
    return text[:200]


async def _get_owned_run(db: AsyncSession, run_id: uuid.UUID, current: dict) -> LiveRun:
    run = await db.get(LiveRun, run_id)
    if run is None or str(run.user_id) != current["sub"]:
        raise HTTPException(status_code=404, detail="实盘任务不存在")
    return run


async def _require_role(db: AsyncSession, current: dict, roles: tuple[str, ...]) -> User:
    user = await db.get(User, _user_id(current))
    if user is None or (user.role not in roles and not user.is_superuser):
        raise HTTPException(status_code=403, detail="权限不足")
    return user


# ---------------------------------------------------------------------------
# Wizard step 3: admission check (GET-style POST, read-only)
# ---------------------------------------------------------------------------

@router.post("/wizard/check-admission", response_model=AdmissionOut)
async def check_admission(
    payload: AdmissionCheckRequest, current: CurrentUser, db: DBSession
) -> AdmissionOut:
    try:
        res = await evaluate_admission(
            db, user_id=_user_id(current), strategy_id=payload.strategy_id,
            version=payload.version, account_id=payload.account_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AdmissionOut(
        strategy_id=res.strategy_id, strategy_version=res.strategy_version,
        account_id=res.account_id, allowed=res.allowed,
        gates=[GateOut(**{
            "key": g.key, "label": g.label, "passed": g.passed,
            "reason": g.reason, "metrics": g.metrics,
        }) for g in res.gates],
        decision_metrics=res.decision_metrics,
    )


# ---------------------------------------------------------------------------
# Deploy (POST /live) — wizard final step
# ---------------------------------------------------------------------------

@router.post("", response_model=LiveRunOut, status_code=status.HTTP_201_CREATED)
async def deploy_live_run(
    payload: LiveCreate, current: CurrentUser, db: DBSession, request: Request
) -> LiveRun:
    user_id = _user_id(current)
    if not settings.LIVE_ENABLED:
        raise HTTPException(status_code=403, detail="实盘功能未开放（平台灰度中）")

    strategy = await db.get(Strategy, payload.strategy_id)
    if strategy is None or str(strategy.user_id) != current["sub"]:
        raise HTTPException(status_code=404, detail="策略不存在")
    if strategy.status == "archived":
        raise HTTPException(status_code=409, detail="已归档策略不可部署实盘")
    vrow = (await db.execute(
        select(StrategyVersion).where(
            StrategyVersion.strategy_id == strategy.id,
            StrategyVersion.version == payload.version,
        )
    )).scalar_one_or_none()
    if vrow is None:
        raise HTTPException(status_code=404, detail=f"版本 v{payload.version} 不存在")
    source = vrow.source_code
    if not source.strip():
        raise HTTPException(status_code=422, detail="策略代码为空，无法部署实盘")

    acct = await db.get(MT5Account, payload.account_id)
    if acct is None or (str(acct.user_id) != current["sub"] and acct.user_id is not None):
        raise HTTPException(status_code=404, detail="MT5 账户不存在")

    # 实盘/模拟互斥（后端双保险）：scope 决定账户类型 ——
    # demo=模拟盘交易（仅模拟账户，免准入闸，订单发往 MT5 模拟服务器）；
    # real=实盘（仅实盘账户，全闸）。
    # ① 绑定行类型；② 节点当前登录实况（探活不可达时跳过，由 ① 与准入闸兜底）。
    if payload.scope == "demo":
        if acct.account_type != "demo":
            raise HTTPException(
                status_code=409,
                detail="模拟盘交易仅支持模拟账户，当前绑定的是实盘/竞赛账户；"
                "请先在总览页切换登录到模拟账户",
            )
    else:
        if acct.account_type != "real":
            raise HTTPException(
                status_code=409,
                detail="实盘仅支持实盘账户，当前绑定的是模拟/竞赛账户；"
                "请先在总览页切换登录到实盘账户",
            )
    active_type = await probe_active_account_type(db, acct.gateway_node_id)
    if active_type is not None and active_type != payload.scope:
        label = "模拟盘交易" if payload.scope == "demo" else "实盘"
        raise HTTPException(
            status_code=409,
            detail=f"{label}要求当前登录的是{'模拟' if payload.scope == 'demo' else '实盘'}账户，"
            "请先在总览页切换登录",
        )

    # P4 EB-1: EA 桥节点的实盘完整链路 EB-2/EB-3 开放。
    if acct.gateway_node_id is not None:
        acct_node = await db.get(GatewayNode, acct.gateway_node_id)
        if acct_node is not None and acct_node.type == "ea_bridge":
            raise HTTPException(
                status_code=409,
                detail="EA 桥节点的实盘将在下一版本开放，敬请期待",
            )

    # 二次确认：账户尾号（PRD 不可跳过）
    if payload.account_tail != str(acct.login)[-4:]:
        raise HTTPException(status_code=422, detail="账户尾号确认不匹配")

    # ── admission：real=four gates, no bypass；demo=模拟盘交易免闸 ──
    if payload.scope == "demo":
        adm = None
    else:
        adm = await evaluate_admission(
            db, user_id=user_id, strategy_id=payload.strategy_id,
            version=payload.version, account_id=payload.account_id,
        )
        if not adm.allowed:
            failed = [g for g in adm.gates if not g.passed]
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "准入闸门未全部通过，无法部署实盘",
                    "failed_gates": [
                        {"key": g.key, "label": g.label, "reason": g.reason}
                        for g in failed
                    ],
                },
            )

    # concurrency cap
    active = (await db.execute(
        select(func.count()).select_from(LiveRun).where(
            LiveRun.user_id == user_id,
            LiveRun.status.in_(["pending", "running", "paused"]),
        )
    )).scalar_one()
    if active >= settings.LIVE_MAX_CONCURRENT:
        raise HTTPException(
            status_code=409,
            detail=f"同时运行的实盘数量已达上限（{settings.LIVE_MAX_CONCURRENT}）",
        )

    obs_days = payload.observation_days or settings.LIVE_OBSERVATION_DAYS
    is_demo_scope = payload.scope == "demo"
    # magic is assigned at deploy time so reconciliation ownership is fixed
    # even before the run spawns (and survives restart/resume).
    magic = settings.LIVE_MAGIC_BASE + (uuid.uuid4().int & 0xFFFFFF)
    admission_snapshot = (
        {"scope": "demo", "gates": "skipped", "note": "模拟盘交易免准入四闸"}
        if is_demo_scope else adm.snapshot()
    )
    snapshot = {
        "execution_mode": "standard",
        "assumption_text": describe_execution_mode("standard")
        + " 实盘成交价为券商真实价格，点差/滑点以实际回报为准。",
        "disclaimer_ack": payload.disclaimer_ack,
        "seed": random.randrange(2**31),
        "strategy_version": payload.version,
        "source_hash": _source_hash(source),
        "sdk": _dependency_versions(),
        "account": {"login": acct.login, "server": acct.server,
                    "type": acct.account_type},
        "admission": admission_snapshot,
    }
    run = LiveRun(
        user_id=user_id,
        strategy_id=payload.strategy_id,
        account_id=payload.account_id,
        source_code=source,
        strategy_version=payload.version,
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        execution_mode="standard",
        allocated_capital=_dec(payload.allocated_capital),
        leverage=payload.leverage,
        magic=magic,
        warmup_bars=payload.warmup_bars,
        parameters=payload.parameters,
        config_snapshot=snapshot,
        admission_snapshot=admission_snapshot,
        observation_ends_at=None if is_demo_scope
        else datetime.now(UTC) + timedelta(days=obs_days),
        capital_cap_pct=100 if is_demo_scope else settings.LIVE_OBSERVATION_CAPITAL_PCT,
        balance=_dec(0.0), equity=_dec(0.0), margin_free=_dec(0.0),
        created_by=user_id,
    )
    db.add(run)
    await db.flush()

    # run-scoped risk rules (frozen at deploy; editable later via rule API)
    for spec in payload.rules:
        db.add(RiskRule(
            scope="run", run_id=run.id, account_id=acct.id,
            type=spec.type, threshold=_dec(spec.threshold, 6),
            action=spec.action, enabled=True,
            note=auto_rule_note(spec.type, spec.threshold, spec.action),
            created_by=user_id,
        ))
    await db.commit()
    await db.refresh(run)

    await audit_record("admission_pass", actor=_actor(current, request),
                       target_type="strategy", target_id=str(run.strategy_id),
                       params={"version": run.strategy_version,
                               "account_id": str(acct.id),
                               "scope": payload.scope,
                               "gates": admission_snapshot["gates"]})
    await audit_record("live_deploy", actor=_actor(current, request),
                       target_type="live_run", target_id=str(run.id),
                       params={"symbol": run.symbol, "timeframe": run.timeframe,
                               "allocated_capital": float(run.allocated_capital),
                               "rules": [r.model_dump() for r in payload.rules],
                               "scope": payload.scope,
                               "observation_days": obs_days})
    run.account_type = acct.account_type  # type: ignore[attr-defined]  # 序列化读取
    get_live_manager().spawn_start(str(run.id))
    return run


# ---------------------------------------------------------------------------
# List / detail / trades / logs
# ---------------------------------------------------------------------------

@router.get("", response_model=LiveListResponse)
async def list_live_runs(
    current: CurrentUser, db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=_PAGE_SIZE_MAX),
    strategy_id: uuid.UUID | None = Query(default=None),
    account_id: uuid.UUID | None = Query(default=None),
    account_type: str | None = Query(
        default=None, description="demo|real|contest — 模拟盘交易/实盘分栏"
    ),
    status_filter: str | None = Query(default=None, alias="status"),
) -> LiveListResponse:
    filters = [LiveRun.user_id == _user_id(current)]
    if strategy_id is not None:
        filters.append(LiveRun.strategy_id == strategy_id)
    if account_id is not None:
        filters.append(LiveRun.account_id == account_id)
    if account_type:
        if account_type not in ("demo", "real", "contest"):
            raise HTTPException(status_code=422, detail="无效的账户类型筛选值")
        filters.append(LiveRun.account_id.in_(
            select(MT5Account.id).where(MT5Account.account_type == account_type)
        ))
    if status_filter:
        if status_filter not in _RUN_STATUSES:
            raise HTTPException(status_code=422, detail="无效的状态筛选值")
        filters.append(LiveRun.status == status_filter)
    total = (await db.execute(
        select(func.count()).select_from(LiveRun).where(*filters)
    )).scalar_one()
    result = await db.execute(
        select(LiveRun).where(*filters).order_by(LiveRun.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    items = list(result.scalars())
    # 批量补 account_type（列表页 模拟盘交易/实盘 分栏标记）
    ids = {it.account_id for it in items}
    type_map: dict = {}
    if ids:
        rows = await db.execute(
            select(MT5Account.id, MT5Account.account_type).where(MT5Account.id.in_(ids))
        )
        type_map = {rid: rtype for rid, rtype in rows.all()}
    items_out = []
    for it in items:
        out = LiveRunOut.model_validate(it)
        out.account_type = type_map.get(it.account_id)
        items_out.append(out)
    return LiveListResponse(
        items=items_out,
        total=total, page=page, page_size=page_size,
        total_pages=max(math.ceil(total / page_size), 1),
    )


@router.get("/{run_id}/trades", response_model=LiveTradeListResponse)
async def list_trades(
    run_id: uuid.UUID, current: CurrentUser, db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=_PAGE_SIZE_MAX),
) -> LiveTradeListResponse:
    run = await _get_owned_run(db, run_id, current)
    total = (await db.execute(
        select(func.count()).select_from(LiveTrade).where(LiveTrade.run_id == run.id)
    )).scalar_one()
    result = await db.execute(
        select(LiveTrade).where(LiveTrade.run_id == run.id)
        .order_by(LiveTrade.open_time.desc(), LiveTrade.deal_ticket.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    return LiveTradeListResponse(items=list(result.scalars()), total=total)


@router.get("/{run_id}/logs", response_model=LiveLogListResponse)
async def list_logs(
    run_id: uuid.UUID, current: CurrentUser, db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=200, ge=1, le=1000),
    kind: str | None = Query(default=None, description="log | signal | system | risk"),
    level: str | None = Query(default=None),
) -> LiveLogListResponse:
    run = await _get_owned_run(db, run_id, current)
    if kind is not None and kind not in ("log", "signal", "system", "risk"):
        raise HTTPException(status_code=422, detail="无效的 kind 筛选值")
    filters = [LiveLog.run_id == run.id]
    if kind:
        filters.append(LiveLog.kind == kind)
    if level:
        filters.append(LiveLog.level == level)
    total = (await db.execute(
        select(func.count()).select_from(LiveLog).where(*filters)
    )).scalar_one()
    result = await db.execute(
        select(LiveLog).where(*filters).order_by(LiveLog.seq.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    return LiveLogListResponse(items=list(result.scalars()), total=total)


# ---------------------------------------------------------------------------
# Control: pause / resume / stop / reset
# ---------------------------------------------------------------------------

@router.post("/{run_id}/pause", response_model=LiveRunOut)
async def pause_live_run(
    run_id: uuid.UUID, current: CurrentUser, db: DBSession, request: Request
) -> LiveRun:
    run = await _get_owned_run(db, run_id, current)
    if run.status not in ("running", "paused"):
        raise HTTPException(status_code=409, detail="只有运行中的实盘可以暂停")
    new_status = await get_live_manager().pause(str(run_id))
    if new_status == "not_found":
        raise HTTPException(status_code=404, detail="实盘任务不存在")
    await audit_record("live_pause", actor=_actor(current, request),
                       target_type="live_run", target_id=str(run_id))
    await db.refresh(run)
    return run


@router.post("/{run_id}/resume", response_model=LiveRunOut)
async def resume_live_run(
    run_id: uuid.UUID, current: CurrentUser, db: DBSession, request: Request
) -> LiveRun:
    run = await _get_owned_run(db, run_id, current)
    if run.status != "paused":
        raise HTTPException(status_code=409, detail="只有已暂停的实盘可以恢复")
    if run.demoted:
        raise HTTPException(status_code=409, detail="策略已被自动降级，需风控中心人工复核后恢复")
    new_status = await get_live_manager().resume(str(run_id))
    if new_status == "not_found":
        raise HTTPException(status_code=404, detail="实盘任务不存在")
    await audit_record("live_resume", actor=_actor(current, request),
                       target_type="live_run", target_id=str(run_id))
    await db.refresh(run)
    return run


@router.post("/{run_id}/reset-anchor", response_model=LiveRunOut)
async def reset_live_anchor(
    run_id: uuid.UUID, current: CurrentUser, db: DBSession, request: Request
) -> LiveRun:
    """网格策略专用：把锚定价重置为当前最新买价（storage["anchor"]，运行时生效）。"""
    run = await _get_owned_run(db, run_id, current)
    if run.status not in ("running", "paused"):
        raise HTTPException(status_code=409, detail="只有运行中/已暂停的实盘可以重置锚点")
    result = await get_live_manager().reset_anchor(str(run_id))
    if result != "ok":
        raise HTTPException(
            status_code=409, detail="策略进程未就绪（启动回放中或已退出），无法重置锚点"
        )
    await audit_record("live_reset_anchor", actor=_actor(current, request),
                       target_type="live_run", target_id=str(run_id))
    await db.refresh(run)
    return run


@router.post("/{run_id}/stop", response_model=LiveRunOut)
async def stop_live_run(
    run_id: uuid.UUID, payload: LiveStopRequest,
    current: CurrentUser, db: DBSession, request: Request
) -> LiveRun:
    run = await _get_owned_run(db, run_id, current)
    if run.status in ("stopped", "failed"):
        raise HTTPException(status_code=409, detail="实盘已处于终态，无法停止")
    new_status = await get_live_manager().stop(str(run_id), payload.close_positions)
    if new_status == "not_found":
        raise HTTPException(status_code=404, detail="实盘任务不存在")
    await audit_record("live_stop", actor=_actor(current, request),
                       target_type="live_run", target_id=str(run_id),
                       params={"close_positions": payload.close_positions})
    await db.refresh(run)
    return run


@router.delete("/{run_id}", status_code=204)
async def delete_live_run(
    run_id: uuid.UUID, current: CurrentUser, db: DBSession, request: Request
) -> None:
    """删除已终态（已停止/失败）的任务记录；运行中的任务必须先停止。"""
    run = await _get_owned_run(db, run_id, current)
    if run.status not in ("stopped", "failed"):
        raise HTTPException(status_code=409, detail="任务尚未停止，请先停止后再删除")
    await db.delete(run)
    await db.commit()
    await audit_record("live_delete", actor=_actor(current, request),
                       target_type="live_run", target_id=str(run_id),
                       params={"status": run.status})


@router.post("/{run_id}/reset", response_model=LiveRunOut)
async def reset_live_run(
    run_id: uuid.UUID, current: CurrentUser, db: DBSession, request: Request
) -> LiveRun:
    run = await _get_owned_run(db, run_id, current)
    if run.status in ("pending", "running", "paused"):
        raise HTTPException(status_code=409, detail="请先停止实盘，再重置")
    if run.demoted:
        raise HTTPException(status_code=409, detail="策略已被自动降级，重置需人工复核")
    new_status = await get_live_manager().reset(str(run_id))
    if new_status == "not_found":
        raise HTTPException(status_code=404, detail="实盘任务不存在")
    await audit_record("live_reset", actor=_actor(current, request),
                       target_type="live_run", target_id=str(run_id))
    await db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# Kill Switch (all live runs, ≤3s, double confirm)
# ---------------------------------------------------------------------------

@router.post("/kill-switch", status_code=status.HTTP_202_ACCEPTED)
async def kill_switch(
    payload: KillSwitchRequest, current: CurrentUser, db: DBSession, request: Request
) -> dict:
    if payload.confirm.strip().upper() != "KILL":
        raise HTTPException(status_code=422, detail="二次确认失败：请输入 KILL")
    await _require_role(db, current, ("admin", "trader"))
    runs = (await db.execute(
        select(LiveRun).where(LiveRun.user_id == _user_id(current),
                              LiveRun.status.in_(["pending", "running", "paused"]))
    )).scalars().all()
    ids = [str(r.id) for r in runs]
    await audit_record("kill_switch", actor=_actor(current, request),
                       target_type="system", target_id="",
                       params={"run_ids": ids})
    # fire-and-forget: the manager closes real positions + stops every run
    get_live_manager().spawn_kill_all(ids)
    return {"status": "executing", "runs": len(ids)}


# ---------------------------------------------------------------------------
# Re-admission after auto-demotion (admin, double confirm, audited)
# ---------------------------------------------------------------------------

@router.post("/{run_id}/re-admit", response_model=LiveRunOut)
async def re_admit(
    run_id: uuid.UUID, payload: ReAdmitRequest,
    current: CurrentUser, db: DBSession, request: Request
) -> LiveRun:
    if payload.confirm.strip().upper() != "READMIT":
        raise HTTPException(status_code=422, detail="二次确认失败：请输入 READMIT")
    await _require_role(db, current, ("admin",))
    run = await _get_owned_run(db, run_id, current)
    if not run.demoted:
        raise HTTPException(status_code=409, detail="该任务未处于降级状态")
    run.demoted = False
    acct = await db.get(MT5Account, run.account_id)
    if acct is not None:
        acct.live_allowed = True
        db.add(acct)
    db.add(run)
    await db.commit()
    await audit_record("re_admit", actor=_actor(current, request),
                       target_type="live_run", target_id=str(run.id),
                       params={"review_note": payload.review_note,
                               "account_login": acct.login if acct else None})
    await db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# Feature-flag probe: the sidebar hides the live entry when disabled
# (PRD 权限闸 — the deploy endpoint rejects server-side regardless).
# ---------------------------------------------------------------------------

@router.get("/meta/status", response_model=dict)
async def live_status(current: CurrentUser) -> dict:
    return {
        "enabled": settings.LIVE_ENABLED,
        "manual_trade_enabled": settings.MANUAL_TRADE_ENABLED,
        "max_concurrent": settings.LIVE_MAX_CONCURRENT,
        "min_paper_days": settings.LIVE_ADMISSION_MIN_PAPER_DAYS,
        "observation_days": settings.LIVE_OBSERVATION_DAYS,
    }


# ---------------------------------------------------------------------------
# Risk center: rules CRUD (audited), risk events, alerts, unfreeze
# ---------------------------------------------------------------------------

@router.post("/rules", response_model=RiskRuleOut, status_code=201)
async def create_rule(
    payload: RiskRuleCreate, current: CurrentUser, db: DBSession, request: Request
) -> RiskRule:
    await _require_role(db, current, ("admin", "trader"))
    if payload.scope == "account" and payload.account_id is None:
        raise HTTPException(status_code=422, detail="account 级规则必须提供 account_id")
    if payload.scope == "run" and payload.run_id is None:
        raise HTTPException(status_code=422, detail="run 级规则必须提供 run_id")
    rule = RiskRule(
        scope=payload.scope, account_id=payload.account_id, run_id=payload.run_id,
        type=payload.type, threshold=_dec(payload.threshold, 6),
        cooldown_minutes=int(payload.cooldown_minutes) if payload.cooldown_minutes else None,
        window_minutes=int(payload.window_minutes) if payload.window_minutes else None,
        apply_to=payload.apply_to,
        action=payload.action, enabled=True,
        note=(payload.note or "").strip() or auto_rule_note(
            payload.type, payload.threshold, payload.action, payload.apply_to,
            cooldown_minutes=payload.cooldown_minutes,
            window_minutes=payload.window_minutes,
        ),
        created_by=_user_id(current),
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    await audit_record("rule_change", actor=_actor(current, request),
                       target_type="risk_rule", target_id=str(rule.id),
                       params={"op": "create", "new": {
                           "type": rule.type, "threshold": payload.threshold,
                           "cooldown_minutes": rule.cooldown_minutes,
                           "window_minutes": rule.window_minutes,
                           "apply_to": rule.apply_to,
                           "action": rule.action, "scope": rule.scope}})
    return rule


@router.get("/rules", response_model=list[RiskRuleOut])
async def list_rules(
    current: CurrentUser, db: DBSession,
    account_id: uuid.UUID | None = Query(default=None),
    run_id: uuid.UUID | None = Query(default=None),
) -> list[RiskRule]:
    filters = []
    if account_id is not None:
        filters.append(RiskRule.account_id == account_id)
    if run_id is not None:
        filters.append(RiskRule.run_id == run_id)
    result = await db.execute(
        select(RiskRule).where(*filters).order_by(RiskRule.created_at.desc())
    )
    return list(result.scalars())


@router.patch("/rules/{rule_id}", response_model=RiskRuleOut)
async def update_rule(
    rule_id: uuid.UUID, payload: RiskRuleUpdate,
    current: CurrentUser, db: DBSession, request: Request
) -> RiskRule:
    await _require_role(db, current, ("admin", "trader"))
    rule = await db.get(RiskRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    old = {"threshold": float(rule.threshold), "action": rule.action,
           "enabled": rule.enabled}
    if payload.threshold is not None:
        rule.threshold = _dec(payload.threshold, 6)
    if payload.cooldown_minutes is not None:
        rule.cooldown_minutes = int(payload.cooldown_minutes)
    if payload.window_minutes is not None:
        rule.window_minutes = int(payload.window_minutes)
    if payload.apply_to is not None:
        rule.apply_to = payload.apply_to
    if payload.action is not None:
        rule.action = payload.action
    if payload.enabled is not None:
        rule.enabled = payload.enabled
    if payload.note is not None:
        rule.note = payload.note
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    await audit_record("rule_change", actor=_actor(current, request),
                       target_type="risk_rule", target_id=str(rule.id),
                       params={"op": "update", "old": old,
                               "new": {"threshold": float(rule.threshold),
                                       "action": rule.action,
                                       "enabled": rule.enabled}})
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: uuid.UUID, current: CurrentUser, db: DBSession, request: Request
) -> None:
    await _require_role(db, current, ("admin", "trader"))
    rule = await db.get(RiskRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    await db.delete(rule)
    await db.commit()
    await audit_record("rule_change", actor=_actor(current, request),
                       target_type="risk_rule", target_id=str(rule_id),
                       params={"op": "delete", "old": {"type": rule.type,
                                                        "threshold": float(rule.threshold)}})


@router.get("/risk-events", response_model=RiskEventListResponse)
async def list_risk_events(
    current: CurrentUser, db: DBSession,
    account_id: uuid.UUID | None = Query(default=None),
    run_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=_PAGE_SIZE_MAX),
) -> RiskEventListResponse:
    filters = []
    if account_id is not None:
        filters.append(RiskEvent.account_id == account_id)
    if run_id is not None:
        filters.append(RiskEvent.run_id == run_id)
    total = (await db.execute(
        select(func.count()).select_from(RiskEvent).where(*filters)
    )).scalar_one()
    result = await db.execute(
        select(RiskEvent).where(*filters).order_by(RiskEvent.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    return RiskEventListResponse(items=list(result.scalars()), total=total)


@router.get("/alerts", response_model=AlertListResponse)
async def list_alerts(
    current: CurrentUser, db: DBSession,
    unread_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=_PAGE_SIZE_MAX),
) -> AlertListResponse:
    filters = []
    if unread_only:
        filters.append(AlertEvent.is_read.is_(False))
    total = (await db.execute(
        select(func.count()).select_from(AlertEvent).where(*filters)
    )).scalar_one()
    unread = (await db.execute(
        select(func.count()).select_from(AlertEvent).where(AlertEvent.is_read.is_(False))
    )).scalar_one()
    result = await db.execute(
        select(AlertEvent).where(*filters).order_by(AlertEvent.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    return AlertListResponse(items=list(result.scalars()), total=total, unread=unread)


@router.post("/alerts/read-all", response_model=dict)
async def mark_all_alerts_read(
    current: CurrentUser, db: DBSession
) -> dict:
    from sqlalchemy import update

    result = await db.execute(
        update(AlertEvent).where(AlertEvent.is_read.is_(False))
        .values(is_read=True)
    )
    await db.commit()
    return {"status": "ok", "marked": int(result.rowcount or 0)}


@router.post("/alerts/{alert_id}/read", response_model=AlertOut)
async def mark_alert_read(
    alert_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> AlertEvent:
    row = await db.get(AlertEvent, alert_id)
    if row is None:
        raise HTTPException(status_code=404, detail="告警不存在")
    row.is_read = True
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.post("/accounts/{account_id}/unfreeze", response_model=dict)
async def unfreeze_account(
    account_id: uuid.UUID, payload: UnfreezeRequest,
    current: CurrentUser, db: DBSession, request: Request
) -> dict:
    """Manual unfreeze after a reconcile mismatch (admin, double confirm, audited)."""
    if payload.confirm.strip().upper() != "UNFREEZE":
        raise HTTPException(status_code=422, detail="二次确认失败：请输入 UNFREEZE")
    await _require_role(db, current, ("admin",))
    acct = await db.get(MT5Account, account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="MT5 账户不存在")
    if not acct.trade_frozen:
        raise HTTPException(status_code=409, detail="账户未处于冻结状态")
    acct.trade_frozen = False
    db.add(acct)
    await db.commit()
    await audit_record("reconcile_unfreeze", actor=_actor(current, request),
                       target_type="mt5_account", target_id=str(account_id),
                       params={"review_note": payload.review_note,
                               "login": acct.login})
    return {"status": "ok"}


# ── run detail: registered LAST so the uuid path param cannot shadow the
#    literal GET routes above (/rules, /alerts, /risk-events) ──

@router.get("/{run_id}/deviation", response_model=DeviationReportOut)
async def get_run_deviation(
    run_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> DeviationReportOut:
    """Execution-quality report: actual fills vs theoretical (M4 P1).

    Compute-on-read over live_trades; registered before GET /{run_id} so the
    literal "deviation" segment cannot be captured as a uuid (422).
    """
    await _get_owned_run(db, run_id, current)
    return DeviationReportOut(**await deviation_report(db, run_id))


@router.get("/{run_id}", response_model=LiveRunDetailOut)
async def get_live_run(
    run_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> LiveRun:
    run = await _get_owned_run(db, run_id, current)
    acct = await db.get(MT5Account, run.account_id)
    # 动态附加：序列化层 LiveRunDetailOut.account_type 读取（前端区分 模拟盘交易/实盘）
    run.account_type = acct.account_type if acct else None  # type: ignore[attr-defined]
    # 动态附加：子进程 ctx.storage 快照（storage.json），供前端网格线等可视化
    run.strategy_state = _read_strategy_state(str(run_id))  # type: ignore[attr-defined]
    return run
