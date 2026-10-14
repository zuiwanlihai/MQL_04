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
from app.services.audit import AuditActor
from app.services.audit import record as audit_record
from app.services.fault_state import (  # P0-4 统一故障状态机
    FaultState,
    FaultType,
    gate_fault,
    state_transition,
)
from app.services.gateway_client import GatewayClient, GatewayError

logger = logging.getLogger("mt5quant.gateway")


# ---------------------------------------------------------------------------
# Backward-compat shims (tests monkeypatch these; production uses FaultRecord.emit())
# ---------------------------------------------------------------------------

async def alert_emit(
    source: str, title: str, message: str = "", *,
    level: str = "warn", **_kw: object,
) -> None:
    """Legacy shim — forwards to alert.emit().  Do NOT use in new production code."""
    from app.services import alert as _alert  # noqa: WPS433 — 延迟 import 避免顶层循环
    await _alert.emit(source=source, title=title, message=message, level=level)


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
    """PRD M1: 网关异常重启必须显著提示 + 触发告警（不得静默恢复）。

    换成 FaultRecord.emit() 统一故障通道——内部自动走 alert + audit 双通道。
    """
    last = info.get("started_at_iso") or "未知"
    pid = info.get("pid") or "?"
    detail = (
        f"网关进程上次未正常退出（上次启动于 {last}，pid={pid}），已自动恢复。"
        "崩溃期间交易状态可能已变化——请先核对 MT5 持仓是否完整，再继续实盘。"
    )
    await gate_fault(
        fault_type=FaultType.GATEWAY_DISCONNECTED,
        action_taken="检测到网关异常重启，自动恢复",
        node_id=node.id,
        detail=detail,
    ).emit()
    logger.error("gateway %s:%s abnormal restart flagged (prev %s)",
                 node.host, node.port, last)


async def _flag_guard_giveup(node: GatewayNode) -> None:
    """Terminal guard exceeded its restart cap → alert once (PRD 防无限抖动).

    换成 FaultRecord.emit() 统一故障通道——内部自动走 alert + audit 双通道。
    """
    detail = (
        "10 分钟内终端拉起失败已达上限，网关停止自动重试。"
        "请人工启动 MT5 终端并确认登录状态（实盘持仓当前无平台风控保护）。"
    )
    await gate_fault(
        fault_type=FaultType.GATEWAY_GIVEUP,
        action_taken="终端守护已放弃自动拉起，停止重试",
        node_id=node.id,
        detail=detail,
    ).emit()
    logger.error("gateway %s:%s terminal guard gave up", node.host, node.port)


async def _probe_node(db: AsyncSession, node: GatewayNode) -> None:
    """One /health poll + mirror + sampled log row for a single node (M12)."""
    key = f"{node.host}:{node.port}"
    prev_status = node.status

    client = GatewayClient.for_registered_node(f"http://{node.host}:{node.port}")
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
        # P0-4 恢复事件：从 degraded/offline 滑回 online → 记录状态转换
        if new_status == "online" and prev_status in ("offline", "degraded"):
            transition = state_transition(
                from_state=FaultState.FAILED if prev_status == "offline" else FaultState.DEGRADED,
                to_state=FaultState.RUNNING,
                reason=f"网关心跳恢复正常（前态 {prev_status}）",
                related_object={"node_id": str(node.id), "host": node.host, "port": node.port},
            )
            logger.info(transition.to_log())
            # 恢复事件走 audit 但不走 alert（不是紧急事件）
            await audit_record(
                "gateway_recovered",
                actor=AuditActor(actor_type="system"),
                target_type="gateway_node", target_id=str(node.id),
                params={"prev_status": prev_status, **transition.related_object},
            )
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
                metadata["guard_gave_up_alerted"] = "1"
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
        # P0-4 关键观测缺口：网关从 degraded 滑入 offline 零输出——现在补结构化故障
        if new_status == "offline" and prev_status != "offline":
            await gate_fault(
                fault_type=FaultType.GATEWAY_DISCONNECTED,
                action_taken="连续心跳失败达到阈值，判定网关离线",
                node_id=node.id,
                detail=f"{node.host}:{node.port} 连续 {_failures[key]} 次心跳失败",
            ).emit(db=db)
        elif new_status == "degraded" and prev_status == "online":
            # 从 online 首次滑入 degraded：加 warn 级故障（不走 critical）
            await gate_fault(
                fault_type=FaultType.GATEWAY_DISCONNECTED,
                action_taken="心跳失败，网关降级",
                node_id=node.id,
                detail=f"{node.host}:{node.port} 心跳失败（{_failures[key]} 次）",
            ).emit(db=db, level="warn")

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
        except Exception as exc:  # noqa: BLE001 — never let the loop die
            # P0-4 区分 DB 错误 vs 其他异常：DB 挂了要结构化告警，
            # 不能让心跳循环静默吞掉
            _emit_db_unavailable_if_any(exc, source="heartbeat_loop")
            logger.exception("gateway heartbeat iteration failed")
        await asyncio.sleep(settings.GATEWAY_HEARTBEAT_INTERVAL)


def _emit_db_unavailable_if_any(exc: Exception, source: str = "") -> None:
    """后台循环统一的 DB 错误判定 + 结构化 emit（容错，绝不抛）。

    与 reconcile.py 的 _is_db_error 逻辑一致，但用同步 try/except——
    心跳循环上下文方便直接调用。
    """
    import asyncio

    name = type(exc).__name__
    module = type(exc).__module__ or ""
    is_db = (
        "sqlalchemy" in module and name in (
            "OperationalError", "InterfaceError", "DBAPIError",
            "InternalError", "ProgrammingError",
        )
        or ("asyncpg" in module or "psycopg" in module)
        or isinstance(exc, (ConnectionRefusedError, ConnectionError))
    )
    if not is_db:
        return

    try:
        # 在当前事件循环里 fire-and-forget emit，不阻塞循环
        async def _fire():
            await gate_fault(
                fault_type=FaultType.DB_UNAVAILABLE,
                action_taken=f"{source} 遇 DB 错误：{exc}",
                detail=f"{source} exception: {name}: {exc}",
            ).emit(db=None, level="critical")
        loop = asyncio.get_event_loop()
        loop.create_task(_fire())
    except Exception:  # noqa: BLE001 — emit 自身不能杀循环
        logger.exception("DB_UNAVAILABLE emit failed from %s", source)
