"""
Gateway endpoints: node registry and sampled health logs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.dependencies import CurrentUser, DBSession
from app.models import GatewayHealthLog, GatewayNode, MT5Account, User
from app.schemas.gateway import (
    GatewayHealthLogOut,
    GatewayNodeCreate,
    GatewayNodeListResponse,
    GatewayNodeOut,
    HealthLogListResponse,
    PairCodeOut,
    PairOut,
    PairRequest,
    PowerFixRequest,
)
from app.services import pair_store
from app.services.audit import AuditActor
from app.services.audit import record as audit_record
from app.services.gateway_client import GatewayClient, GatewayError
from app.services.gateway_heartbeat import classify, gateway_host_port

router = APIRouter()


def _user_id(current: dict) -> uuid.UUID:
    return uuid.UUID(current["sub"])


async def _require_admin(db, current: dict, action: str = "生成配对码") -> None:
    user = await db.get(User, _user_id(current))
    if user is None or (user.role != "admin" and not user.is_superuser):
        raise HTTPException(status_code=403, detail=f"权限不足：仅管理员可{action}")


@router.post("/pair-codes", response_model=PairCodeOut, status_code=201)
async def create_pair_code(
    current: CurrentUser, db: DBSession, request: Request
) -> PairCodeOut:
    """Mint a one-shot pairing code (10 min) + QR for a fresh gateway install."""
    from app.config import settings
    from app.services.totp import qr_svg_data_uri  # reuse pure-python QR factory

    await _require_admin(db, current)
    payload = await pair_store.create(created_by=current["sub"])
    uri = f"mt5gw://pair?server={settings.PAIR_SERVER_URL}&code={payload.code}"
    data_uri = qr_svg_data_uri(uri)
    qr_svg = data_uri.split(",", 1)[1] if "," in data_uri else ""
    await audit_record(
        "gateway_pair", actor=AuditActor(
            actor_type="user", actor_id=_user_id(current),
            actor_label=str(current.get("email") or current["sub"])[:120],
            source_ip=request.client.host if request.client else "",
        ),
        target_type="system", target_id="gateway_pair_code",
        params={"op": "create_code", "expires_at": payload.expires_at.isoformat()},
    )
    return PairCodeOut(
        code=f"{payload.code[:4]}-{payload.code[4:]}",
        expires_at=payload.expires_at,
        server_url=settings.PAIR_SERVER_URL,
        qr_svg=qr_svg,
    )


@router.post("/pair", response_model=PairOut)
async def pair_gateway(
    payload: PairRequest, db: DBSession, request: Request
) -> PairOut:
    """Gateway-side pairing: one-shot code → node upsert + shared API key.

    Unauthenticated by design (the gateway has no credentials yet); protected
    by the one-shot code, its short TTL, and a per-IP rate limit.
    """
    from app.config import settings

    ip = request.client.host if request.client else ""
    if pair_store.rate_limited(ip):
        raise HTTPException(status_code=429, detail="配对尝试过于频繁，请稍后再试")

    consumed = await pair_store.consume(payload.code)
    if consumed is None:
        raise HTTPException(status_code=404, detail="配对码无效或已过期")

    node = (await db.execute(
        select(GatewayNode).where(
            GatewayNode.host == payload.host, GatewayNode.port == payload.port
        )
    )).scalar_one_or_none()
    if node is None:
        node = GatewayNode(
            name=payload.name, host=payload.host, port=payload.port,
            status="starting",
        )
        db.add(node)
    else:
        node.name = payload.name or node.name
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="网关节点注册冲突") from None
    await db.refresh(node)

    await audit_record(
        "gateway_pair", actor=AuditActor(
            actor_type="system", actor_label=f"gateway:{payload.host}:{payload.port}",
            source_ip=ip,
        ),
        target_type="gateway_node", target_id=str(node.id),
        params={"op": "pair", "paired_by": consumed.created_by},
    )
    return PairOut(
        node_id=node.id, name=node.name, host=node.host, port=node.port,
        api_key=settings.GATEWAY_API_KEY,
        backend_version=settings.APP_VERSION,
    )


def _node_out(node: GatewayNode) -> GatewayNodeOut:
    """Map the ORM row (``metadata_json`` column ``metadata``) to the API."""
    return GatewayNodeOut(
        id=node.id,
        name=node.name,
        type=node.type,  # type: ignore[arg-type]
        host=node.host,
        port=node.port,
        status=node.status,  # type: ignore[arg-type]
        version=node.version,
        uptime_seconds=node.uptime_seconds,
        active_sessions=node.active_sessions,
        cpu_percent=node.cpu_percent,
        memory_percent=node.memory_percent,
        last_heartbeat=node.last_heartbeat,
        is_primary=node.is_primary,
        metadata={str(k): str(v) for k, v in (node.metadata_json or {}).items()},
        created_at=node.created_at,
    )


@router.get("/nodes", response_model=GatewayNodeListResponse)
async def list_nodes(
    current: CurrentUser,
    db: DBSession,
) -> GatewayNodeListResponse:
    result = await db.execute(
        select(GatewayNode).order_by(GatewayNode.is_primary.desc(), GatewayNode.created_at)
    )
    nodes = list(result.scalars())
    return GatewayNodeListResponse(
        items=[_node_out(n) for n in nodes], total=len(nodes)
    )


@router.post(
    "/nodes", response_model=GatewayNodeOut, status_code=status.HTTP_201_CREATED
)
async def create_node(
    payload: GatewayNodeCreate,
    current: CurrentUser,
    db: DBSession,
) -> GatewayNodeOut:
    """Register a node manually — probed immediately (offline on failure).

    管理员操作：注册表就是「平台自己认可的网关」这份名单，而平台会给名单里的
    每一个地址送 ``X-Gateway-Key``（心跳每几秒一次）。名单能由任意用户添加，
    「只发给已注册节点」就等于没有约束——先注册一台自己的主机，再等心跳上门。
    """
    await _require_admin(db, current, "注册网关节点")

    existing = await db.execute(
        select(GatewayNode).where(
            GatewayNode.host == payload.host, GatewayNode.port == payload.port
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="该网关节点已存在")

    url = f"http://{payload.host}:{payload.port}"
    node_status = "offline"
    version = ""
    uptime = 0.0
    cpu = 0.0
    memory = 0.0
    heartbeat_at: datetime | None = None
    metadata: dict[str, str] = {}

    client = GatewayClient(url)
    try:
        health = await client.health()
    except GatewayError:
        health = None  # probe failure → keep status=offline
    finally:
        await client.aclose()

    if health is not None:
        node_status = classify(health)
        version = str(health.get("version") or "")
        uptime = float(health.get("uptime_seconds") or 0)
        cpu = float(health.get("cpu_percent") or 0)
        memory = float(health.get("memory_percent") or 0)
        heartbeat_at = datetime.now(timezone.utc)
        if health.get("mt5_version"):
            metadata["mt5_version"] = str(health["mt5_version"])
        if health.get("error"):
            metadata["error"] = str(health["error"])

    is_configured = (payload.host, payload.port) == gateway_host_port()
    node = GatewayNode(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        status=node_status,
        version=version,
        uptime_seconds=uptime,
        cpu_percent=cpu,
        memory_percent=memory,
        last_heartbeat=heartbeat_at,
        is_primary=is_configured,
        metadata_json=metadata,
    )
    db.add(node)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="该网关节点已存在") from None
    await db.refresh(node)
    return _node_out(node)


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: uuid.UUID, current: CurrentUser, db: DBSession,
) -> None:
    """Remove a node; health logs cascade via the FK.

    Guarded: MT5Account.gateway_node_id is ON DELETE CASCADE, so deleting a
    node would silently unbind every account on it — reject the primary node
    and any node that still has bound accounts.  Admin-only, like
    ``create_node``: it is the same trusted-host list.
    """
    await _require_admin(db, current, "删除网关节点")
    node = await db.get(GatewayNode, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="网关节点不存在")
    if node.is_primary:
        raise HTTPException(status_code=409, detail="主网关节点不可删除")
    bound = (await db.execute(
        select(func.count()).select_from(MT5Account).where(
            MT5Account.gateway_node_id == node_id)
    )).scalar_one()
    if bound > 0:
        raise HTTPException(
            status_code=409,
            detail=f"该节点仍绑定 {bound} 个 MT5 账户，请先解绑后再删除",
        )
    await db.delete(node)
    await db.commit()


@router.get("/health-logs", response_model=HealthLogListResponse)
async def list_health_logs(
    current: CurrentUser,
    db: DBSession,
    node_id: uuid.UUID | None = Query(default=None, description="按节点过滤"),
    limit: int = Query(default=500, ge=1, le=500),
) -> HealthLogListResponse:
    """Most recent health observations (``recorded_at`` DESC)."""
    filters = [GatewayHealthLog.node_id == node_id] if node_id else []
    total = (
        await db.execute(
            select(func.count())
            .select_from(GatewayHealthLog)
            .where(*filters)
        )
    ).scalar_one()
    result = await db.execute(
        select(GatewayHealthLog)
        .where(*filters)
        .order_by(GatewayHealthLog.recorded_at.desc())
        .limit(limit)
    )
    logs = list(result.scalars())
    return HealthLogListResponse(
        items=[
            GatewayHealthLogOut(
                id=log.id,
                node_id=log.node_id,
                status=log.status,  # type: ignore[arg-type]
                latency_ms=log.latency_ms,
                error_rate=log.error_rate,
                message=log.message,
                recorded_at=log.recorded_at,
            )
            for log in logs
        ],
        total=total,
    )


# ---------------------------------------------------------------------------
# Ops passthrough (M11): one-click self-check + power-plan fix
# ---------------------------------------------------------------------------

@router.get("/selfcheck")
async def run_selfcheck(current: CurrentUser) -> dict:
    """Proxy the gateway's checklist to the Web UI (5s UI budget → 10s client)."""
    client = GatewayClient()
    try:
        return await client.selfcheck()
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    finally:
        await client.aclose()


@router.post("/power/fix")
async def power_fix(
    payload: PowerFixRequest, current: CurrentUser, db: DBSession, request: Request
) -> dict:
    """Guidance-only by default; ``execute=true`` mutates the host power plan.

    Audited either way — PRD M7 ①「配置变更」；真实执行额外标注 executed。
    """
    ip = request.client.host if request.client else ""
    actor = AuditActor(actor_type="user",
                       actor_id=uuid.UUID(current["sub"]),
                       actor_label=str(current.get("email") or current["sub"])[:120],
                       source_ip=ip or "")
    client = GatewayClient()
    try:
        result = await client.power_fix(execute=payload.execute)
    except GatewayError as exc:
        await audit_record("gateway_power_fix", actor=actor,
                           target_type="system", target_id="power_plan",
                           params={"execute": payload.execute},
                           result="failure", error_code="GATEWAY_ERROR")
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    finally:
        await client.aclose()
    await audit_record("gateway_power_fix", actor=actor,
                       target_type="system", target_id="power_plan",
                       params={"execute": payload.execute,
                               "commands": result.get("commands", [])})
    return result
