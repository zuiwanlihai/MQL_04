"""Reconciliation + gateway-offline watchdog (M6 Phase E).

Two background loops started from the lifespan when LIVE_ENABLED:

``reconcile_loop`` — every ``LIVE_RECONCILE_SECONDS`` compares the platform's
position mirror against the gateway's real ``positions_get`` for every account
with a live run.  A mismatch (either direction) freezes the account's
auto-trading (pre-trade gate rejects all), raises a critical alert, writes a
RiskEvent(source=reconcile) and an audit row.  Per PRD the platform NEVER
auto-closes on recovery — the diff is surfaced for the user to decide.

``offline_watchdog`` — tracks the gateway node status; once it has been
non-online for longer than ``LIVE_GATEWAY_OFFLINE_ALERT_SECONDS`` while any
live run holds positions, it alerts「实盘持仓当前无保护」once per episode and
re-alerts on recovery with the reconcile-diff instruction.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.config import settings
from app.engine.session import AsyncSessionLocal
from app.models import (
    GatewayNode,
    LiveRun,
    MT5Account,
    RiskEvent,
)
from app.services.alert import emit as alert_emit
from app.services.audit import AuditActor
from app.services.audit import record as audit_record
from app.services.fault_state import (  # P0-4 统一故障状态机
    FaultType,
    gate_fault,
)
from app.services.gateway_client import GatewayClient, GatewayError
from app.services.gateway_route import resolve_gateway

logger = logging.getLogger("mt5quant.live.reconcile")


def _tickets(positions: list[dict]) -> set[int]:
    return {int(p.get("ticket", 0)) for p in positions}


async def reconcile_once() -> dict[str, int]:
    """One reconcile pass over every account with a live run.

    M12: each account is compared against the gateway node it is bound to
    (MT5 single-terminal = one account per node), never the global default.
    Returns {account_id: mismatch_count} (0 = consistent).  Freezing is
    idempotent; an already-frozen account stays frozen until manually
    unfrozen (re-admit flow).
    """
    result: dict[str, int] = {}
    async with AsyncSessionLocal() as db:
        account_ids = (await db.execute(
            select(LiveRun.account_id)
            .where(LiveRun.status.in_(["running", "paused", "stopping"]))
            .distinct()
        )).scalars().all()
        # resolve each account's node URL once (single-terminal routing).
        node_url: dict = {}
        for account_id in account_ids:
            node_url[account_id] = await resolve_gateway(db, account_id)
    if not account_ids:
        return result

    for account_id in account_ids:
        client = GatewayClient.for_registered_node(node_url[account_id] or None)
        try:
            try:
                real = await client.positions()
            except GatewayError as exc:
                logger.warning("reconcile %s: gateway unreachable: %s",
                               account_id, exc.detail)
                # P0-4 零观测缺口：reconcile 循环静默跳过网关不可达
                try:
                    await gate_fault(
                        fault_type=FaultType.GATEWAY_DISCONNECTED,
                        action_taken=f"对账时网关不可达，跳过账户 {account_id}",
                        account_id=account_id,
                        detail=f"reconcile gateway unreachable: {exc.detail}",
                    ).emit(db=None, level="warn")
                except Exception:  # noqa: BLE001 — emit 不能阻断对账循环
                    logger.exception("reconcile gateway-unreachable fault emit failed")
                continue
            async with AsyncSessionLocal() as db:
                runs = (await db.execute(
                    select(LiveRun).where(
                        LiveRun.account_id == account_id,
                        LiveRun.status.in_(["running", "paused", "stopping"]),
                    )
                )).scalars().all()
                mirrored = [p for r in runs for p in (r.positions or [])]
                # ownership: only compare tickets tagged with these runs' magic
                magics = {r.magic for r in runs}
                real_mine = [p for p in real if int(p.get("magic", 0)) in magics]
                mirrored_mine = [p for p in mirrored
                                 if int(p.get("magic", 0)) in magics]
                diff = _tickets(real_mine) ^ _tickets(mirrored_mine)
                result[str(account_id)] = len(diff)
                if not diff:
                    # converge any unknown-state orders (timed-out RPCs)
                    for r in runs:
                        if r.reconcile_pending:
                            r.reconcile_pending = False
                            db.add(r)
                    await db.commit()
                    continue
                acct = await db.get(MT5Account, account_id)
                if acct is not None and not acct.trade_frozen:
                    acct.trade_frozen = True
                    db.add(acct)
                db.add(RiskEvent(
                    run_id=runs[0].id if runs else None,
                    account_id=account_id,
                    rule_type="reconcile_mismatch", source="reconcile",
                    threshold=Decimal(0), observed=Decimal(len(diff)),
                    action_taken="freeze",
                    detail={"diff_tickets": sorted(diff),
                            "gateway": len(real_mine), "platform": len(mirrored_mine)},
                ))
                await db.commit()
                # P0-4 替换原有 alert_emit + audit_record → FaultRecord 统一通道
                # （内部自动覆盖 alert + audit；RiskEvent 单独写入保留，属于风险域独立记录）
                await gate_fault(
                    fault_type=FaultType.POSITION_MISMATCH,
                    action_taken=f"冻结账户自动交易，差异 {len(diff)} 笔",
                    run_id=runs[0].id if runs else None,
                    account_id=account_id,
                    detail=f"diff_tickets={sorted(diff)} gateway={len(real_mine)} "
                           f"platform={len(mirrored_mine)}",
                    recovery_override="平台不会自动平仓，请人工核对 MT5 终端后在风控中心解冻。",
                ).emit(db=db, level="critical")
                logger.warning("reconcile mismatch account=%s diff=%s — frozen",
                               account_id, sorted(diff))
        finally:
            await client.aclose()
    return result


async def reconcile_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=settings.LIVE_RECONCILE_SECONDS
            )
            return
        except asyncio.TimeoutError:
            pass
        try:
            await reconcile_once()
        except Exception as exc:  # noqa: BLE001 — loop must survive
            # P0-4 区分 DB 错误 vs 其他异常：DB 挂了要结构化告警
            if _is_db_error(exc):
                try:
                    await gate_fault(
                        fault_type=FaultType.DB_UNAVAILABLE,
                        action_taken=f"对账循环遇 DB 错误：{exc}",
                        detail=f"reconcile_loop exception: {type(exc).__name__}: {exc}",
                    ).emit(db=None, level="critical")
                except Exception:  # noqa: BLE001 — emit 不能杀循环
                    logger.exception("reconcile_loop DB_UNAVAILABLE emit failed")
            logger.exception("reconcile pass failed")


# ── gateway offline watchdog (风险真空) ────────────────────────

async def _exposed_offline_seconds(db) -> float:
    """Max offline time among nodes that hold an account with live positions.

    M12: the risk vacuum is per-node — the primary node being online does not
    protect a run bound to a different (now offline) node, and vice versa.
    """
    rows = (await db.execute(
        select(GatewayNode.status, GatewayNode.last_heartbeat)
        .join(MT5Account, MT5Account.gateway_node_id == GatewayNode.id)
        .join(LiveRun, LiveRun.account_id == MT5Account.id)
        .where(LiveRun.status.in_(["running", "paused"]))
    )).all()
    worst = 0.0
    now = datetime.now(UTC)
    for status, last_hb in rows:
        if status == "online":
            continue
        if last_hb is None:
            worst = float("inf")
            continue
        worst = max(worst, (now - last_hb).total_seconds())
    return worst


async def _live_positions_exist(db) -> bool:
    runs = (await db.execute(
        select(LiveRun.positions).where(
            LiveRun.status.in_(["running", "paused"])
        )
    )).scalars().all()
    return any(bool(pos) for pos in runs)


async def offline_watchdog(stop_event: asyncio.Event) -> None:
    """Alert once per offline episode when live positions are unprotected."""
    alerted = False
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=settings.LIVE_POLL_SECONDS
            )
            return
        except asyncio.TimeoutError:
            pass
        try:
            async with AsyncSessionLocal() as db:
                off = await _exposed_offline_seconds(db)
                exposed = await _live_positions_exist(db)
            if (off > settings.LIVE_GATEWAY_OFFLINE_ALERT_SECONDS and exposed
                    and not alerted):
                alerted = True
                # P0-4 替换原有 alert_emit + audit_record → 统一故障通道
                await gate_fault(
                    fault_type=FaultType.GATEWAY_DISCONNECTED,
                    action_taken=f"网关掉线 {int(off)} 秒，实盘持仓当前无保护",
                    detail="网关离线期间平台风控无法执行且无券商侧保护单；"
                           "恢复后平台将先对账并呈现差异，不会自动平仓。",
                    recovery_override="请检查网关/终端；恢复后平台将先对账并呈现差异，由您人工决策。",
                ).emit(db=None, level="critical")
            elif off == 0.0 and alerted:
                alerted = False
                # P0-4 恢复事件：info 级 alert，不写 audit（正常恢复）
                await alert_emit(
                    "risk_vacuum", "网关已恢复，请核对离线期间持仓",
                    "对账任务将在下个周期比对差异；差异持仓不会被自动平掉。",
                    level="info",
                )
        except Exception as exc:  # noqa: BLE001
            # P0-4 同 reconcile_loop：DB 错误结构化告警，其他异常保持 log
            if _is_db_error(exc):
                try:
                    await gate_fault(
                        fault_type=FaultType.DB_UNAVAILABLE,
                        action_taken=f"离线看门狗遇 DB 错误：{exc}",
                        detail=f"offline_watchdog exception: {type(exc).__name__}: {exc}",
                    ).emit(db=None, level="critical")
                except Exception:  # noqa: BLE001
                    logger.exception("offline_watchdog DB_UNAVAILABLE emit failed")
            logger.exception("offline watchdog iteration failed")


def _is_db_error(exc: Exception) -> bool:
    """判断异常是否属于 DB 连接/可用性错误。

    SQLAlchemy 的 sqlalchemy.exc.OperationalError / InterfaceError / DBAPIError
    以及 asyncpg/psycopg2 的 ConnectionRefusedError / ConnectionError / TimeoutError
    都算。不枚举具体子类名——用字符串匹配覆盖所有 DB driver。
    """
    name = type(exc).__name__
    module = type(exc).__module__ or ""
    if "sqlalchemy" in module:
        return name in ("OperationalError", "InterfaceError", "DBAPIError",
                        "InternalError", "ProgrammingError")
    if "asyncpg" in module or "psycopg" in module:
        return True  # asyncpg/psycopg2 连接相关异常都算 DB error
    return isinstance(exc, (ConnectionRefusedError, ConnectionError))
