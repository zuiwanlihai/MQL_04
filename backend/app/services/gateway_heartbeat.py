"""
Gateway heartbeat service.

Background loop polls the configured gateway's ``/health`` every few
seconds, mirrors the result onto ``gateway_nodes`` and writes *sampled*
rows into ``gateway_health_logs`` (status change always logs; otherwise
at most one row per ``HEALTH_LOG_SAMPLE_INTERVAL``).  Rows older than
``HEALTH_LOG_RETENTION_HOURS`` are pruned every 10 minutes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from time import perf_counter
from urllib.parse import urlparse

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.engine.session import AsyncSessionLocal
from app.models import GatewayHealthLog, GatewayNode
from app.services.alert import emit as alert_emit
from app.services.audit import AuditActor
from app.services.audit import record as audit_record
from app.services.gateway_client import GatewayClient, GatewayError

logger = logging.getLogger("mt5quant.gateway")

# -- module-level heartbeat state (single loop per process) --------------------
_failures: dict[str, int] = {}  # "host:port" → consecutive failed polls
_next_cleanup_at = 0.0  # unix ts of the next retention sweep


def gateway_host_port(url: str | None = None) -> tuple[str, int]:
    """Extract (host, port) from a gateway base URL (defaults to settings)."""
    parsed = urlparse(url or settings.GATEWAY_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8090
    return host, port


def classify(health: dict) -> str:
    """Gateway ``/health`` payload → node status (ok ⇒ online)."""
    return "online" if health.get("status") == "ok" else "degraded"


async def ensure_default_node(db: AsyncSession) -> GatewayNode:
    """Get or create the row representing the configured local gateway."""
    host, port = gateway_host_port()
    result = await db.execute(
        select(GatewayNode).where(
            GatewayNode.host == host, GatewayNode.port == port
        )
    )
    node = result.scalar_one_or_none()
    if node is None:
        node = GatewayNode(name="本地网关", host=host, port=port, status="starting")
        db.add(node)
        await db.commit()
        await db.refresh(node)
    return node


async def _recent_error_rate(db: AsyncSession, node_id) -> float:
    """Failed-poll ratio over the 5-minute sliding window."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    result = await db.execute(
        select(GatewayHealthLog.status)
        .where(
            GatewayHealthLog.node_id == node_id,
            GatewayHealthLog.recorded_at >= cutoff,
        )
    )
    statuses = list(result.scalars())
    if not statuses:
        return 0.0
    bad = sum(1 for s in statuses if s != "online")
    return round(bad / len(statuses), 4)


async def _flag_abnormal_restart(node: GatewayNode, info: dict) -> None:
    """PRD M1: 网关异常重启必须显著提示 + 触发告警（不得静默恢复）。"""
    last = info.get("started_at_iso") or "未知"
    pid = info.get("pid") or "?"
    message = (
        f"网关进程上次未正常退出（上次启动于 {last}，pid={pid}），已自动恢复。"
        "崩溃期间交易状态可能已变化——请先核对 MT5 持仓是否完整，再继续实盘。"
    )
    await alert_emit(
        source="gateway", level="critical",
        title="网关异常重启：请先核对持仓",
        message=message,
    )
    await audit_record(
        "gateway_abnormal_restart",
        actor=AuditActor(actor_type="system"),
        target_type="gateway_node", target_id=str(node.id),
        params={"last_started_at": last, "last_pid": pid,
                "host": node.host, "port": node.port},
    )
    logger.error("gateway %s:%s abnormal restart flagged (prev %s)",
                 node.host, node.port, last)


async def _flag_guard_giveup(node: GatewayNode) -> None:
    """Terminal guard exceeded its restart cap → alert once (PRD 防无限抖动)."""
    await alert_emit(
        source="gateway", level="critical",
        title="MT5 终端守护已放弃自动拉起",
        message="10 分钟内终端拉起失败已达上限，网关停止自动重试。"
                "请人工启动 MT5 终端并确认登录状态（实盘持仓当前无平台风控保护）。",
    )
    await audit_record(
        "gateway_guard_giveup",
        actor=AuditActor(actor_type="system"),
        target_type="gateway_node", target_id=str(node.id),
        params={"host": node.host, "port": node.port},
    )
    logger.error("gateway %s:%s terminal guard gave up", node.host, node.port)


async def _probe_node(db: AsyncSession, node: GatewayNode) -> None:
    """One /health poll + mirror + sampled log row for a single node (M12)."""
    key = f"{node.host}:{node.port}"
    prev_status = node.status

    client = GatewayClient(f"http://{node.host}:{node.port}")
    started = perf_counter()
    health: dict | None = None
    error_message: str | None = None
    try:
        health = await client.health()
    except GatewayError as exc:
        error_message = exc.detail
    finally:
        latency_ms = round((perf_counter() - started) * 1000, 1)
        await client.aclose()

    if health is not None:
        _failures[key] = 0
        new_status = classify(health)
        node.status = new_status
        node.last_heartbeat = datetime.now(timezone.utc)
        node.version = str(health.get("version") or "")
        node.uptime_seconds = float(health.get("uptime_seconds") or 0)
        node.cpu_percent = float(health.get("cpu_percent") or 0)
        node.memory_percent = float(health.get("memory_percent") or 0)
        node.active_sessions = int(health.get("active_sessions") or 0)
        metadata: dict[str, str] = dict(node.metadata_json or {})
        for src, dst in (("mt5_version", "mt5_version"), ("os", "os")):
            if health.get(src):
                metadata[dst] = str(health[src])
        if health.get("error"):
            metadata["error"] = str(health["error"])
        elif "error" in metadata:
            del metadata["error"]
        node.metadata_json = metadata
        if new_status != "online":
            error_message = str(health.get("error") or "")
        # M11: gateway crashed last time (stale state file) → one-time alert.
        # Deduped by the crash event's started_at so a persistent flag does not
        # re-alert every heartbeat tick (PRD: 异常重启必须显著提示 + 告警).
        if health.get("abnormal_restart"):
            info = health.get("abnormal_restart_info") or {}
            sig = str(info.get("started_at_iso") or info.get("pid") or "unknown")
            if metadata.get("abnormal_restart_alerted") != sig:
                metadata["abnormal_restart_alerted"] = sig
                node.metadata_json = metadata
                await _flag_abnormal_restart(node, info)
        # Terminal guard gave up (restart cap hit) → alert once until recovered.
        if health.get("guard_gave_up"):
            if not metadata.get("guard_gave_up_alerted"):
                metadata["guard_gave_up_alerted"] = True
                node.metadata_json = metadata
                await _flag_guard_giveup(node)
        elif metadata.get("guard_gave_up_alerted"):
            del metadata["guard_gave_up_alerted"]  # recovered → re-arm
            node.metadata_json = metadata
    else:
        # 防抖：第 1 次失败 → degraded；连续 offline_threshold 秒 → offline
        _failures[key] = _failures.get(key, 0) + 1
        threshold = max(
            2, settings.GATEWAY_OFFLINE_THRESHOLD // max(settings.GATEWAY_HEARTBEAT_INTERVAL, 1)
        )
        new_status = "offline" if _failures[key] >= threshold else "degraded"
        node.status = new_status

    # -- sampled logging: status change always, otherwise ≥ interval ----------
    now = datetime.now(timezone.utc)
    last_at = (
        await db.execute(
            select(GatewayHealthLog.recorded_at)
            .where(GatewayHealthLog.node_id == node.id)
            .order_by(GatewayHealthLog.recorded_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    due = last_at is None or (now - last_at).total_seconds() >= settings.HEALTH_LOG_SAMPLE_INTERVAL
    if new_status != prev_status or due:
        db.add(
            GatewayHealthLog(
                node_id=node.id,
                status=new_status,
                latency_ms=latency_ms,
                error_rate=await _recent_error_rate(db, node.id),
                message=error_message or None,
            )
        )


async def heartbeat_once(db: AsyncSession) -> None:
    """One poll cycle over EVERY registered node (M12 multi-gateway).

    The configured default node is upserted first (legacy single-node setups
    keep working), then all registered nodes are probed against their own
    host:port — an account bound to a remote node must show that node's real
    status, not the local gateway's.
    """
    global _next_cleanup_at

    await ensure_default_node(db)
    nodes = (await db.execute(
        select(GatewayNode).order_by(GatewayNode.is_primary.desc(),
                                      GatewayNode.created_at)
    )).scalars().all()
    for node in nodes:
        if node.type == "ea_bridge":
            # EA 桥节点没有 HTTP /health 可探（host 是 ea:{server}:{login} 标识，
            # port=0）；活跃度由 /ea/sync 直接刷新 last_heartbeat，此处跳过，
            # 防止心跳循环每 5s 对 EA 节点发一次注定失败的请求刷日志。
            continue
        await _probe_node(db, node)

    # -- retention sweep every 10 minutes -------------------------------------
    now = datetime.now(timezone.utc)
    if now.timestamp() >= _next_cleanup_at:
        cutoff = now - timedelta(hours=settings.HEALTH_LOG_RETENTION_HOURS)
        await db.execute(
            delete(GatewayHealthLog).where(GatewayHealthLog.recorded_at < cutoff)
        )
        _next_cleanup_at = now.timestamp() + 600

    await db.commit()


async def heartbeat_loop() -> None:
    """Forever loop — started from the FastAPI lifespan when enabled."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await heartbeat_once(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never let the loop die
            logger.exception("gateway heartbeat iteration failed")
        await asyncio.sleep(settings.GATEWAY_HEARTBEAT_INTERVAL)
