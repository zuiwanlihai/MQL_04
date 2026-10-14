"""EA bridge endpoints (P4 方案 B, EB-1).

Called by the MQL5 EA (``deploy/ea/EAQuantBridge.mq5``) over WebRequest:

  * ``POST /ea/pair``   — one-shot admin code → upsert ``ea_bridge`` node,
    returns the ``node_key`` credential (shown once, hashed at rest).
  * ``POST /ea/sync``   — authenticated by ``X-EA-Key``; ingests ticks into
    :data:`app.services.ea_bridge.registry` and returns queued commands.
  * ``POST /ea/result`` — resolves a pending command future (req_id RPC).

Tick ingestion is deliberately NOT audited (noise); pairing is (ea.node_pair).
The node_key never appears in logs or any other response.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.config import settings
from app.dependencies import DBSession
from app.models import GatewayNode
from app.schemas.ea_bridge import (
    EACommand,
    EAPairRequest,
    EAPairResponse,
    EAResultRequest,
    EAResultResponse,
    EASyncRequest,
    EASyncResponse,
)
from app.services import pair_store
from app.services.audit import AuditActor
from app.services.audit import record as audit_record
from app.services.ea_bridge import registry

router = APIRouter()

EA_SYNC_INTERVAL_MS = 500  # matches the EA's EventSetMillisecondTimer default


def _enabled() -> None:
    if not settings.EA_BRIDGE_ENABLED:
        raise HTTPException(status_code=404, detail="EA 桥未启用")


def _key_hash(node_key: str) -> str:
    return hashlib.sha256(node_key.encode("utf-8")).hexdigest()


async def _require_ea_node(db: DBSession, request: Request) -> GatewayNode:
    """Authenticate via ``X-EA-Key`` → the matching ea_bridge node (401)."""
    raw = request.headers.get("X-EA-Key", "").strip()
    node = None
    if raw:
        node = (await db.execute(
            select(GatewayNode).where(GatewayNode.ea_key_hash == _key_hash(raw))
        )).scalar_one_or_none()
    if node is None or node.type != "ea_bridge":
        raise HTTPException(status_code=401, detail="无效的 EA 节点密钥")
    return node


@router.post("/pair", response_model=EAPairResponse)
async def ea_pair(payload: EAPairRequest, db: DBSession, request: Request) -> EAPairResponse:
    """EA-side pairing: one-shot code → node upsert + node_key credential.

    Unauthenticated by design (the EA has no credentials yet); protected by
    the one-shot code, its TTL, and the shared per-IP rate limit.
    """
    _enabled()
    ip = request.client.host if request.client else ""
    if pair_store.rate_limited(ip):
        raise HTTPException(status_code=429, detail="配对尝试过于频繁，请稍后再试")

    consumed = await pair_store.consume(payload.pair_code)
    if consumed is None:
        raise HTTPException(status_code=409, detail="配对码无效或已过期")

    host = f"ea:{payload.mt5_server}:{payload.mt5_login}"
    node = (await db.execute(
        select(GatewayNode).where(
            GatewayNode.host == host, GatewayNode.port == 0
        )
    )).scalar_one_or_none()
    if node is None:
        node = GatewayNode(
            name=f"EA·{payload.mt5_login}",
            type="ea_bridge", host=host, port=0, status="starting",
        )
        db.add(node)
    else:
        node.type = "ea_bridge"  # re-pair of a previously known terminal
    node_key = secrets.token_hex(32)  # shown once; only its hash is stored
    node.ea_key_hash = _key_hash(node_key)
    meta = dict(node.metadata_json or {})
    for k, v in (payload.terminal_info or {}).items():
        meta[str(k)[:40]] = str(v)[:120]
    meta.pop("error", None)
    node.metadata_json = meta
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=409, detail="EA 节点注册冲突") from None
    await db.refresh(node)
    registry.drop_session(str(node.id))  # stale buffers from a previous key

    await audit_record(
        "ea.node_pair",
        actor=AuditActor(
            actor_type="system",
            actor_label=f"ea:{payload.mt5_server}:{payload.mt5_login}",
            source_ip=ip,
        ),
        target_type="gateway_node", target_id=str(node.id),
        params={"op": "pair", "mt5_login": payload.mt5_login,
                "mt5_server": payload.mt5_server,
                "paired_by": consumed.created_by},
    )
    return EAPairResponse(
        node_id=str(node.id),
        node_key=node_key,
        backend_version=settings.APP_VERSION,
        sync_interval_ms=EA_SYNC_INTERVAL_MS,
    )


@router.post("/sync", response_model=EASyncResponse)
async def ea_sync(payload: EASyncRequest, request: Request, db: DBSession) -> EASyncResponse:
    """Tick upload + command download in one round-trip (EA poll cadence)."""
    _enabled()
    node = await _require_ea_node(db, request)
    node_id = str(node.id)

    for symbol, rows in (payload.ticks or {}).items():
        sym = str(symbol).upper()[:32]
        registry.push_ticks(
            node_id, sym, rows, max_rows=settings.EA_SYNC_MAX_TICKS,
        )
    if payload.account:
        registry.set_account(node_id, payload.account)

    commands = [EACommand.model_validate(c) for c in registry.drain_commands(node_id)]
    registry.touch(node_id, time.time())

    # EA 节点无 HTTP 探活（心跳循环按 type 跳过）——sync 即最后活跃证明。
    node.status = "online"
    node.last_heartbeat = datetime.now(timezone.utc)
    await db.commit()

    return EASyncResponse(
        commands=commands,
        server_time_ms=int(time.time() * 1000),
        next_wait_ms=EA_SYNC_INTERVAL_MS,
    )


@router.post("/result", response_model=EAResultResponse)
async def ea_result(payload: EAResultRequest, request: Request, db: DBSession) -> EAResultResponse:
    """Command result callback (req_id RPC); stale req_ids resolve False."""
    _enabled()
    node = await _require_ea_node(db, request)
    delivered = registry.resolve_result(
        str(node.id), payload.req_id, payload.ok, payload.payload, payload.error,
    )
    return EAResultResponse(resolved=delivered)
