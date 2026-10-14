"""Alert-rule evaluation loop (M15, PRD M7 告警规则 P0).

Background loop that evaluates the user-configurable ``alert_rules`` table
against the DB mirrors (live/paper run account mirrors, gateway node status,
trade tables) and emits ``AlertEvent`` rows (source=``alert_rule``) through
the standard ``services.alert.emit`` path — so the M10 channel fan-out
applies automatically.

Design invariants:
  * Read-only against mirrors — never calls the gateway (a dead gateway must
    not stall the loop; ``gateway_offline`` works off the heartbeat mirror).
  * Per-subject cooldown suppression (in-memory): a rule that keeps being
    breached re-alerts at most once per ``cooldown_seconds``.
  * Trade-cursor seeding: the first observation of a run's trade table only
    records the cursor — historical fills never flood alerts on boot.
  * Any iteration error is logged and swallowed; the loop never dies.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.config import settings
from app.engine.session import AsyncSessionLocal
from app.models import (
    AlertRule,
    GatewayNode,
    LiveRun,
    LiveTrade,
    MT5Account,
    PaperRun,
    PaperTrade,
)
from app.services.alert import emit as alert_emit

logger = logging.getLogger("mt5quant.alert_rules")

# (rule_id, subject_key) → unix ts of last fired alert (cooldown suppression)
_last_fired: dict[tuple[str, str], float] = {}
# (run_kind, run_id) → max trade ticket seen; absent = not yet seeded
_trade_cursor: dict[tuple[str, str], int] = {}
# node_id → unix ts when it first appeared non-online (gateway_offline)
_offline_since: dict[str, float] = {}

_LIVE_ACTIVE = ("running", "paused")


def _f(v) -> float:
    return float(v) if v is not None else 0.0


def _now() -> datetime:
    return datetime.now(UTC)


def _cooldown_ok(rule_id: str, subject: str, cooldown_seconds: int) -> bool:
    key = (rule_id, subject)
    last = _last_fired.get(key)
    if last is not None and _now().timestamp() - last < cooldown_seconds:
        return False
    _last_fired[key] = _now().timestamp()
    return True


def compare(op: str, value: float, threshold: float) -> bool:
    if op == "lt":
        return value < threshold
    if op == "lte":
        return value <= threshold
    if op == "gt":
        return value > threshold
    if op == "gte":
        return value >= threshold
    return False


async def _fire(rule: AlertRule, subject: str, title: str, message: str,
               *, account_id=None, run_id=None) -> bool:
    if not _cooldown_ok(str(rule.id), subject, rule.cooldown_seconds):
        return False
    await alert_emit(
        source="alert_rule", level=rule.level,
        title=f"[{rule.name}] {title}"[:200], message=message,
        account_id=account_id, run_id=run_id,
    )
    return True


def _run_ids_for_rule(rule: AlertRule, runs) -> list:
    """Runs this rule may see: only its owner's; account scope narrows further.

    ``runs`` items are either ORM run rows or ``(kind, run)`` tuples.
    """
    def _row(r):
        return r[1] if isinstance(r, tuple) else r

    owned = [r for r in runs if str(_row(r).user_id) == str(rule.user_id)]
    if rule.scope == "account" and rule.account_id is not None:
        return [r for r in owned if _row(r).account_id is not None
                and str(_row(r).account_id) == str(rule.account_id)]
    return owned


async def _eval_drawdown(db, rules_by_user: dict) -> int:
    """drawdown_pct over live runs (peak_equity) + paper runs (equity_history)."""
    fired = 0
    rules = [r for rs in rules_by_user.values() for r in rs if r.type == "drawdown_pct"]
    if not rules:
        return 0

    live = (await db.execute(
        select(LiveRun).where(LiveRun.user_id.in_(list(rules_by_user)),
                              LiveRun.status.in_(_LIVE_ACTIVE))
    )).scalars().all()
    paper = (await db.execute(
        select(PaperRun).where(PaperRun.user_id.in_(list(rules_by_user)),
                               PaperRun.status.in_(_LIVE_ACTIVE))
    )).scalars().all()

    for rule in rules:
        for run in _run_ids_for_rule(rule, live):
            peak = _f(run.peak_equity)
            if peak <= 0:
                continue
            dd = (peak - _f(run.equity)) / peak * 100.0
            if dd >= _f(rule.threshold) and dd > 0:
                if await _fire(
                    rule, f"live:{run.id}", "实盘回撤触发",
                    f"实盘任务 {run.symbol} 权益自峰值回撤 {dd:.2f}%"
                    f"（阈值 {rule.threshold}%，峰值 {peak:.2f}，当前 {float(run.equity):.2f}）。",
                    account_id=run.account_id, run_id=run.id,
                ):
                    fired += 1
        for run in _run_ids_for_rule(rule, paper):
            history = run.equity_history or []
            if not history:
                continue
            peak = max(_f(p.get("equity", 0)) for p in history)
            if peak <= 0:
                continue
            dd = (peak - _f(run.equity)) / peak * 100.0
            if dd >= _f(rule.threshold) and dd > 0:
                if await _fire(
                    rule, f"paper:{run.id}", "虚拟下单回撤触发",
                    f"虚拟下单任务 {run.symbol} 权益自峰值回撤 {dd:.2f}%"
                    f"（阈值 {rule.threshold}%，峰值 {peak:.2f}，当前 {float(run.equity):.2f}）。",
                    account_id=run.account_id, run_id=run.id,
                ):
                    fired += 1
    return fired


async def _eval_daily_loss(db, rules_by_user: dict) -> int:
    """daily_loss_pct over live runs (day_start_equity mirror)."""
    fired = 0
    rules = [r for rs in rules_by_user.values() for r in rs if r.type == "daily_loss_pct"]
    if not rules:
        return 0
    live = (await db.execute(
        select(LiveRun).where(LiveRun.user_id.in_(list(rules_by_user)),
                              LiveRun.status.in_(_LIVE_ACTIVE))
    )).scalars().all()
    for rule in rules:
        for run in _run_ids_for_rule(rule, live):
            start = _f(run.day_start_equity)
            if start <= 0:
                continue
            loss = (start - _f(run.equity)) / start * 100.0
            if loss >= _f(rule.threshold) and loss > 0:
                if await _fire(
                    rule, f"live:{run.id}", "当日亏损触发",
                    f"实盘任务 {run.symbol} 当日亏损 {loss:.2f}%"
                    f"（阈值 {rule.threshold}%，日初权益 {start:.2f}，"
                    f"当前 {float(run.equity):.2f}）。",
                    account_id=run.account_id, run_id=run.id,
                ):
                    fired += 1
    return fired


async def _eval_gateway_offline(db, rules_by_user: dict) -> int:
    """gateway_offline: node non-online continuously for >= threshold seconds."""
    fired = 0
    rules = [r for rs in rules_by_user.values() for r in rs if r.type == "gateway_offline"]
    if not rules:
        return 0
    nodes = (await db.execute(select(GatewayNode))).scalars().all()
    now_ts = _now().timestamp()
    for node in nodes:
        nid = str(node.id)
        if node.status == "online":
            _offline_since.pop(nid, None)
            continue
        _offline_since.setdefault(nid, now_ts)
    for rule in rules:
        # scope=account → only the node that account is bound to
        if rule.scope == "account" and rule.account_id is not None:
            acct = await db.get(MT5Account, rule.account_id)
            candidate_ids = {str(acct.gateway_node_id)} if acct else set()
        else:
            candidate_ids = {str(n.id) for n in nodes}
        for node in nodes:
            nid = str(node.id)
            if nid not in candidate_ids or node.status == "online":
                continue
            since = _offline_since.get(nid)
            if since is None:
                continue
            offline_secs = now_ts - since
            if offline_secs >= _f(rule.threshold):
                if await _fire(
                    rule, f"node:{nid}", "网关断线告警",
                    f"网关节点 {node.name}（{node.host}:{node.port}）"
                    f"已持续离线 {offline_secs:.0f} 秒（阈值 {rule.threshold:.0f} 秒），"
                    "状态：" + node.status + "。持仓与自动交易当前无平台风控保护。",
                ):
                    fired += 1
    return fired


async def _eval_trade_filled(db, rules_by_user: dict) -> int:
    """trade_filled: alert on every NEW live/paper trade (cursor-seeded)."""
    fired = 0
    rules = [r for rs in rules_by_user.values() for r in rs if r.type == "trade_filled"]
    if not rules:
        return 0
    live_runs = (await db.execute(
        select(LiveRun).where(LiveRun.user_id.in_(list(rules_by_user)),
                              LiveRun.status.in_(_LIVE_ACTIVE))
    )).scalars().all()
    paper_runs = (await db.execute(
        select(PaperRun).where(PaperRun.user_id.in_(list(rules_by_user)),
                               PaperRun.status.in_(_LIVE_ACTIVE))
    )).scalars().all()
    runs = [("live", r) for r in live_runs] + [("paper", r) for r in paper_runs]

    for rule in rules:
        selected = _run_ids_for_rule(rule, runs)
        for kind, run in selected:
            key = (kind, str(run.id))
            model = LiveTrade if kind == "live" else PaperTrade
            ticket_col = LiveTrade.deal_ticket if kind == "live" else PaperTrade.ticket
            max_ticket = (await db.execute(
                select(func.max(ticket_col)).where(model.run_id == run.id)
            )).scalar()
            max_ticket = int(max_ticket or 0)
            cursor = _trade_cursor.get(key)
            if cursor is None:  # first sight — seed without firing
                _trade_cursor[key] = max_ticket
                continue
            if max_ticket <= cursor:
                continue
            new_count = (await db.execute(
                select(func.count()).select_from(model).where(
                    model.run_id == run.id, ticket_col > cursor)
            )).scalar()
            label = "实盘" if kind == "live" else "虚拟下单"
            if new_count and await _fire(
                rule, f"{kind}:{run.id}", f"{label}成交告警",
                f"{label}任务 {run.symbol} 新增 {new_count} 笔成交"
                f"（最新成交编号 {max_ticket}）。",
                account_id=run.account_id, run_id=run.id,
            ):
                fired += 1
            _trade_cursor[key] = max_ticket
    return fired


async def _eval_custom_metric(db, rules_by_user: dict) -> int:
    """custom_metric: operator/threshold over a live-run mirror field."""
    fired = 0
    rules = [r for rs in rules_by_user.values() for r in rs if r.type == "custom_metric"]
    if not rules:
        return 0
    live = (await db.execute(
        select(LiveRun).where(LiveRun.user_id.in_(list(rules_by_user)),
                              LiveRun.status.in_(_LIVE_ACTIVE))
    )).scalars().all()
    for rule in rules:
        if rule.metric not in {"equity", "balance", "unrealized_pnl", "margin_free"}:
            continue
        for run in _run_ids_for_rule(rule, live):
            value = _f(getattr(run, rule.metric, None))
            if compare(rule.operator, value, _f(rule.threshold)):
                sym = {"lt": "<", "lte": "≤", "gt": ">", "gte": "≥"}[rule.operator]
                if await _fire(
                    rule, f"live:{run.id}", "自定义指标触发",
                    f"实盘任务 {run.symbol} 指标 {rule.metric}={value:.2f} "
                    f"{sym} 阈值 {rule.threshold:.2f}。",
                    account_id=run.account_id, run_id=run.id,
                ):
                    fired += 1
    return fired


async def evaluate_once() -> int:
    """One full evaluation pass; returns the number of alerts fired."""
    async with AsyncSessionLocal() as db:
        rules = (await db.execute(
            select(AlertRule).where(AlertRule.enabled.is_(True))
        )).scalars().all()
        if not rules:
            return 0
        by_user: dict = {}
        for r in rules:
            by_user.setdefault(r.user_id, []).append(r)

        fired = 0
        fired += await _eval_drawdown(db, by_user)
        fired += await _eval_daily_loss(db, by_user)
        fired += await _eval_gateway_offline(db, by_user)
        fired += await _eval_trade_filled(db, by_user)
        fired += await _eval_custom_metric(db, by_user)
        return fired


def reset_state() -> None:
    """Clear in-memory cooldown/cursor state (tests / hot reload)."""
    _last_fired.clear()
    _trade_cursor.clear()
    _offline_since.clear()


def clear_rule_state(rule_id: str) -> None:
    """Drop one rule's cooldown entries (re-tuned rule may fire again now)."""
    for key in [k for k in _last_fired if k[0] == str(rule_id)]:
        del _last_fired[key]


async def rules_loop() -> None:
    """Forever loop — started from the FastAPI lifespan."""
    while True:
        try:
            await evaluate_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never let the loop die
            logger.exception("alert-rule evaluation iteration failed")
        await asyncio.sleep(settings.ALERT_RULES_INTERVAL_SECONDS)
