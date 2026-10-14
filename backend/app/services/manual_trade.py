"""Manual trading service (M14).

Real-money orders typed by the user in the manual panel.  Every action reuses
the SAME safety line as automated live trading (M6):

    resolve the account's gateway node (M12) → explicit login with the stored
    credentials → pre-trade risk gate (``risk.engine.evaluate``) → gateway call
    → audit row (with the risk decision).

A rejected order NEVER reaches the broker.  Manual orders carry ``magic=0``
(the MT5 convention for human orders) so they are excluded from strategy
reconciliation ownership.  Market orders / closes / cancels / pending-order creation are supported —
pending creation goes through the gateway's ``TRADE_ACTION_PENDING`` path.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.engine.session import AsyncSessionLocal
from app.models import AuditLog, MT5Account, RiskEvent
from app.services.audit import AuditActor
from app.services.audit import record as audit_record
from app.services.gateway_client import (
    GatewayClient,
    GatewayError,
    GatewayLoginError,
    GatewayTradeNotAllowed,
)
from app.services.gateway_route import account_credentials, resolve_gateway
from app.services.risk.engine import RiskVerdict, evaluate, load_rules
from app.services.risk.positions import register_opened_position, sync_position_registry

logger = logging.getLogger("mt5quant.manual_trade")


async def _recent_manual_order_count(account_id: str, window_sec: int = 60) -> int:
    """近 window_sec 秒内该账户成功手动下单数（审计日志为准，重启不丢）。

    max_orders_per_minute 规则在手动链路的数据源——引擎的
    orders_this_minute 由调用方传入，手动链路没有实盘任务的内存计数，
    改从审计日志统计。失败返回 0（不阻断下单）。
    """
    try:
        async with AsyncSessionLocal() as db:
            since = datetime.now(UTC) - timedelta(seconds=window_sec)
            n = await db.execute(
                select(func.count()).select_from(AuditLog).where(
                    AuditLog.action == "manual_order",
                    AuditLog.result == "success",
                    AuditLog.target_id == account_id,
                    AuditLog.created_at >= since,
                )
            )
            return int(n.scalar_one() or 0)
    except Exception:  # noqa: BLE001 — 统计失败不阻断下单
        logger.exception("recent manual order count failed")
        return 0


@dataclass
class ManualActor:
    """Who typed the order (from the request) for the audit trail."""

    user_id: uuid.UUID
    email: str
    ip: str


async def _record_risk_event(
    acct: MT5Account, verdict: RiskVerdict, *, op: str, symbol: str,
    volume: float | None, position_id: str | None,
) -> None:
    """手动单被风控拦截时落一条 RiskEvent（风控页"触发事件"可见）。

    记录失败只打日志——拦截结果不受影响，不能因记录失败放行订单。
    """
    try:
        async with AsyncSessionLocal() as db:
            db.add(RiskEvent(
                account_id=acct.id,
                rule_id=uuid.UUID(verdict.rule_id) if verdict.rule_id else None,
                rule_type=verdict.rule_type or "unknown",
                source="manual",
                threshold=Decimal(str(verdict.threshold)),
                observed=Decimal(str(verdict.observed)),
                action_taken=verdict.action or "reject",
                detail={
                    "op": op, "symbol": symbol, "volume": volume,
                    "position_id": position_id,
                    "login": acct.login, "server": acct.server,
                    "message": verdict.error_message,
                },
            ))
            await db.commit()
    except Exception:  # noqa: BLE001 — 记录失败不改变拦截结果
        logger.exception("risk event persist failed")


def _require_enabled() -> None:
    if not settings.MANUAL_TRADE_ENABLED:
        raise HTTPException(
            status_code=409, detail="手动交易功能未启用（灰度开关关闭）",
        )


async def _owned_account(
    db: AsyncSession, account_id: uuid.UUID, current: dict
) -> MT5Account:
    """Ownership model matches live/paper: foreign account → 404, never 403."""
    acct = await db.get(MT5Account, account_id)
    if acct is None or (str(acct.user_id) != current["sub"] and acct.user_id is not None):
        raise HTTPException(status_code=404, detail="MT5 账户不存在")
    return acct


async def _open_client(db: AsyncSession, acct: MT5Account) -> GatewayClient:
    """Route to the account's node and log in with the stored credentials.

    Single-terminal guard (M12 invariant): a manual login on a node where a
    DIFFERENT account has an active live run would steal the MT5 session and
    silently re-point the strategy's orders — refuse with 409 instead.
    """
    from sqlalchemy import select

    from app.models import LiveRun

    if acct.gateway_node_id is not None:
        clash = (await db.execute(
            select(LiveRun.id)
            .join(MT5Account, LiveRun.account_id == MT5Account.id)
            .where(
                LiveRun.status.in_(["running", "paused", "stopping"]),
                MT5Account.gateway_node_id == acct.gateway_node_id,
                LiveRun.account_id != acct.id,
            ).limit(1)
        )).scalars().first()
        if clash is not None:
            raise HTTPException(
                status_code=409,
                detail="该网关节点正被其他账户的实盘任务占用——MT5 单终端同一时刻"
                "只能登录一个账户，手动登录会挤掉实盘会话。请为手动交易使用独立节点。",
            )

    url = await resolve_gateway(db, acct.id)
    creds = await account_credentials(db, acct.id)
    if creds is None:
        raise HTTPException(
            status_code=400,
            detail="绑定账户缺少可用凭据（密码密文缺失或解密失败），请重新绑定账户",
        )
    login, password, server = creds
    client = GatewayClient(url)
    try:
        await client.login(login, password, server)
    except (GatewayLoginError, GatewayError) as exc:
        await client.aclose()
        raise HTTPException(
            status_code=400,
            detail=f"网关登录绑定账户失败（{login}@{server}）：{exc.detail}",
        ) from exc
    return client


def _run_view(acct: MT5Account) -> dict:
    """Minimal run dict for the risk gate: manual orders have no live run, so
    observation ramp / halt / day-loss / drawdown are inert; only the frozen +
    per-order lot / position-count / exposure / margin gates apply."""
    return {
        "account_frozen": acct.trade_frozen,
        "halt_until": "",
        "allocated_capital": 0.0,
        "observation_ends_at": None,
        "lot_cap": 0.0,
        "capital_cap_pct": 100.0,
        "day_start_equity": 0.0,
        "peak_equity": 0.0,
    }


async def _guard(
    client: GatewayClient, acct: MT5Account, *, op: str, symbol: str,
    volume: float | None, position_id: str | None,
) -> RiskVerdict:
    """Pre-trade risk gate against the account's live broker snapshot."""
    account = await client.account() or {}
    positions = await client.positions() or []
    rules = await load_rules(None, str(acct.id))
    # platform hard lot cap on top of the rule set (mis-click guard)
    if op in ("buy", "sell") and volume is not None and volume > settings.MANUAL_TRADE_MAX_LOT:
        verdict = RiskVerdict(
            allow=False, error_code="RISK_BLOCKED",
            error_message=f"手动单笔手数超过平台上限 {settings.MANUAL_TRADE_MAX_LOT} 手",
            action="reject", rule_type="manual_lot_cap",
            threshold=settings.MANUAL_TRADE_MAX_LOT, observed=float(volume),
        )
        await _record_risk_event(acct, verdict, op=op, symbol=symbol,
                                 volume=volume, position_id=position_id)
        return verdict
    recent_deals = None
    closed_trades = None
    registry_open_times = None
    if op in ("buy", "sell") and any(
        r.get("type") in ("consecutive_loss_cooldown", "order_rate_window")
        and str(r.get("apply_to") or "both") in ("both", "manual")
        for r in rules
    ):
        # 优先用持仓登记表（按票号直查成交，不依赖终端历史同步）；
        # 登记失败时回退终端按日期历史。
        closed_trades, registry_open_times = await sync_position_registry(
            client, str(acct.id), positions
        )
        if closed_trades is None:
            try:
                end = int(time.time())
                recent_deals = await client.deals(end - 14 * 86400, end)
            except (GatewayError, GatewayLoginError):
                recent_deals = None  # 历史拉取失败 → 冷却规则本次跳过，不阻断下单
    verdict = evaluate(
        op=op, symbol=symbol, volume=volume, position_id=position_id,
        run=_run_view(acct), account=account, positions=positions, rules=rules,
        recent_deals=recent_deals,
        closed_trades=closed_trades,
        registry_open_times=registry_open_times,
        orders_this_minute=await _recent_manual_order_count(str(acct.id)),
        order_source="manual",
    )
    if not verdict.allow:
        await _record_risk_event(acct, verdict, op=op, symbol=symbol,
                                 volume=volume, position_id=position_id)
    return verdict


def _result_from_gateway(
    action: str, gw: dict, *, error_code: str | None = None,
    error_message: str | None = None,
) -> dict:
    # Broker-side rejection (success=false with no platform error): surface the
    # gateway's own reason (e.g. "Market closed") so the panel doesn't show a
    # bare "未知错误" — the retcode_text alone is often misleading (10018 maps to
    # 资金不足 yet demo servers also return it when the market is shut).
    if not gw.get("success") and error_message is None:
        comment = str(gw.get("comment") or "").strip()
        retcode_text = str(gw.get("retcode_text") or "").strip()
        error_message = (comment or retcode_text or None)
    return {
        "success": bool(gw.get("success")),
        "action": action,
        "retcode": int(gw.get("retcode", -1)),
        "retcode_text": str(gw.get("retcode_text") or ""),
        "order": int(gw.get("order", 0) or 0) or None,
        "deal": int(gw.get("deal", 0) or 0) or None,
        "price": float(gw.get("price", 0.0) or 0.0) or None,
        "volume": float(gw.get("volume", 0.0) or 0.0) or None,
        "error_code": error_code,
        "error_message": error_message,
    }


async def _audit(
    actor: ManualActor, action: str, acct: MT5Account, params: dict,
    *, result: str, verdict: RiskVerdict | None = None,
) -> None:
    await audit_record(
        action, actor=AuditActor(actor_type="user", actor_id=actor.user_id,
                                 actor_label=actor.email[:120], source_ip=actor.ip),
        target_type="mt5_account", target_id=str(acct.id),
        params=params, result=result,
        risk_decision=(None if verdict is None else
                       ("allow" if verdict.allow else "block")),
        rule_id=verdict.rule_id if verdict else None,
    )


async def place_order(
    db: AsyncSession, current: dict, actor: ManualActor, *, account_id: uuid.UUID,
    symbol: str, side: str, volume: float, deviation: int, comment: str,
) -> dict:
    """Market buy/sell on a bound account (risk-gated + audited)."""
    _require_enabled()
    acct = await _owned_account(db, account_id, current)
    params = {"symbol": symbol.upper(), "side": side, "volume": volume,
              "deviation": deviation, "login": acct.login, "server": acct.server}
    if not acct.is_trade_allowed:
        await _audit(actor, "manual_order", acct, params, result="blocked")
        raise HTTPException(status_code=409, detail="该账户当前禁止交易（只读/已禁用）")

    client = await _open_client(db, acct)
    try:
        verdict = await _guard(client, acct, op=side, symbol=symbol.upper(),
                               volume=volume, position_id=None)
        if not verdict.allow:
            await _audit(actor, "manual_order", acct, params, result="blocked",
                         verdict=verdict)
            return _result_from_gateway(
                "order", {"success": False, "retcode": -1},
                error_code=verdict.error_code or "RISK_BLOCKED",
                error_message=verdict.error_message or "风控拦截",
            )
        try:
            gw = await client.place_market(
                symbol.upper(), volume, side, magic=0,
                comment=comment, deviation=deviation,
                timeout=settings.LIVE_ORDER_TIMEOUT,
            )
        except GatewayTradeNotAllowed as exc:
            await _audit(actor, "manual_order", acct, params, result="blocked")
            return _result_from_gateway(
                "order", {"success": False, "retcode": -1},
                error_code="TRADE_NOT_ALLOWED", error_message=exc.detail,
            )
        except GatewayError as exc:
            await _audit(actor, "manual_order", acct, params, result="error")
            return _result_from_gateway(
                "order", {"success": False, "retcode": -1},
                error_code="GATEWAY_ERROR", error_message=exc.detail,
            )
        await _audit(actor, "manual_order", acct, params,
                     result="success" if gw.get("success") else "error",
                     verdict=verdict)
        out = _result_from_gateway("order", gw)
        if out.get("success") and out.get("order"):
            # 立即登记票号（持仓登记表），否则"开平都在两次风控检查之间"的单子
            # 对连亏冷却/窗口限频不可见
            await register_opened_position(
                str(acct.id), int(out["order"]), symbol.upper(),
                open_time=int(time.time()),
            )
        out["account"] = await client.account()
        out["positions"] = await client.positions() or []
        return out
    finally:
        await client.aclose()


async def place_pending(
    db: AsyncSession, current: dict, actor: ManualActor, *, account_id: uuid.UUID,
    symbol: str, order_type: str, volume: float, price: float,
    sl: float | None, tp: float | None, comment: str,
) -> dict:
    """Limit/stop pending order on a bound account (risk-gated + audited).

    The risk gate sees the pending order like a market entry (op derived from
    the order_type prefix) — lot/exposure/margin caps apply up front; the
    reserved margin only becomes real when the order triggers.
    """
    _require_enabled()
    acct = await _owned_account(db, account_id, current)
    side = "buy" if order_type.startswith("buy") else "sell"
    params = {"symbol": symbol.upper(), "order_type": order_type, "volume": volume,
              "price": price, "sl": sl, "tp": tp,
              "login": acct.login, "server": acct.server}
    if not acct.is_trade_allowed:
        await _audit(actor, "manual_pending", acct, params, result="blocked")
        raise HTTPException(status_code=409, detail="该账户当前禁止交易（只读/已禁用）")

    client = await _open_client(db, acct)
    try:
        verdict = await _guard(client, acct, op=side, symbol=symbol.upper(),
                               volume=volume, position_id=None)
        if not verdict.allow:
            await _audit(actor, "manual_pending", acct, params, result="blocked",
                         verdict=verdict)
            return _result_from_gateway(
                "pending", {"success": False, "retcode": -1},
                error_code=verdict.error_code or "RISK_BLOCKED",
                error_message=verdict.error_message or "风控拦截",
            )
        try:
            gw = await client.place_pending(
                symbol.upper(), volume, order_type, price,
                sl=sl, tp=tp, magic=0, comment=comment,
                timeout=settings.LIVE_ORDER_TIMEOUT,
            )
        except GatewayTradeNotAllowed as exc:
            await _audit(actor, "manual_pending", acct, params, result="blocked")
            return _result_from_gateway(
                "pending", {"success": False, "retcode": -1},
                error_code="TRADE_NOT_ALLOWED", error_message=exc.detail,
            )
        except GatewayError as exc:
            await _audit(actor, "manual_pending", acct, params, result="error")
            return _result_from_gateway(
                "pending", {"success": False, "retcode": -1},
                error_code="GATEWAY_ERROR", error_message=exc.detail,
            )
        await _audit(actor, "manual_pending", acct, params,
                     result="success" if gw.get("success") else "error",
                     verdict=verdict)
        out = _result_from_gateway("pending", gw)
        out["account"] = await client.account()
        out["positions"] = await client.positions() or []
        return out
    finally:
        await client.aclose()


async def close_position(
    db: AsyncSession, current: dict, actor: ManualActor, *, account_id: uuid.UUID,
    ticket: int, volume: float | None,
) -> dict:
    """Close (part of) an open position by ticket. Reduces risk → only the
    frozen gate blocks it; lot/exposure/margin caps do not apply to closes."""
    _require_enabled()
    acct = await _owned_account(db, account_id, current)
    params = {"ticket": ticket, "volume": volume,
              "login": acct.login, "server": acct.server}
    client = await _open_client(db, acct)
    try:
        verdict = await _guard(client, acct, op="close", symbol="",
                               volume=volume, position_id=str(ticket))
        if not verdict.allow:
            await _audit(actor, "manual_close", acct, params, result="blocked",
                         verdict=verdict)
            return _result_from_gateway(
                "close", {"success": False, "retcode": -1},
                error_code=verdict.error_code or "RISK_BLOCKED",
                error_message=verdict.error_message or "风控拦截",
            )
        try:
            gw = await client.close_position(
                ticket, volume, magic=0, timeout=settings.LIVE_ORDER_TIMEOUT,
            )
        except GatewayError as exc:
            await _audit(actor, "manual_close", acct, params, result="error")
            return _result_from_gateway(
                "close", {"success": False, "retcode": -1},
                error_code="GATEWAY_ERROR", error_message=exc.detail,
            )
        await _audit(actor, "manual_close", acct, params,
                     result="success" if gw.get("success") else "error",
                     verdict=verdict)
        out = _result_from_gateway("close", gw)
        out["account"] = await client.account()
        out["positions"] = await client.positions() or []
        return out
    finally:
        await client.aclose()


async def cancel_order(
    db: AsyncSession, current: dict, actor: ManualActor, *, account_id: uuid.UUID,
    ticket: int,
) -> dict:
    """Cancel a pending order by ticket."""
    _require_enabled()
    acct = await _owned_account(db, account_id, current)
    params = {"ticket": ticket, "login": acct.login, "server": acct.server}
    client = await _open_client(db, acct)
    try:
        if acct.trade_frozen:
            await _audit(actor, "manual_cancel", acct, params, result="blocked")
            return _result_from_gateway(
                "cancel", {"success": False, "retcode": -1},
                error_code="RISK_BLOCKED",
                error_message="账户因持仓对账不一致被冻结，禁止撤单",
            )
        try:
            gw = await client.cancel_order(ticket, timeout=settings.LIVE_ORDER_TIMEOUT)
        except GatewayError as exc:
            await _audit(actor, "manual_cancel", acct, params, result="error")
            return _result_from_gateway(
                "cancel", {"success": False, "retcode": -1},
                error_code="GATEWAY_ERROR", error_message=exc.detail,
            )
        await _audit(actor, "manual_cancel", acct, params,
                     result="success" if gw.get("success") else "error")
        return _result_from_gateway("cancel", gw)
    finally:
        await client.aclose()


async def snapshot(
    db: AsyncSession, current: dict, *, account_id: uuid.UUID
) -> dict:
    """One login round-trip: account mirror + positions + pending orders."""
    acct = await _owned_account(db, account_id, current)
    client = await _open_client(db, acct)
    try:
        return {
            "account": await client.account(),
            "positions": await client.positions() or [],
            "orders": await client.orders() or [],
        }
    except GatewayError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc
    finally:
        await client.aclose()


async def recent_deals(
    db: AsyncSession, current: dict, *, account_id: uuid.UUID,
    start: int, end: int,
) -> list[dict]:
    acct = await _owned_account(db, account_id, current)
    client = await _open_client(db, acct)
    try:
        return await client.deals(start, end) or []
    except GatewayError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc
    finally:
        await client.aclose()
