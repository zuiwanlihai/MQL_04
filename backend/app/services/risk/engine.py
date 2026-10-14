"""Pre-trade risk gate (M6).

``evaluate`` is a pure function executed by the PARENT process *before* an
order is forwarded to the gateway — the single most important safety line:
a rejected order never reaches the broker.  Rules are resolved from the
``risk_rules`` table with scope precedence ``run > account > global``; the
observation ramp (PRD 阶梯放量) is enforced on top of every rule.

Actions:
    reject             → RISK_BLOCKED back to the strategy, order not sent
    halt_open_today    → additionally blocks all open orders until UTC tomorrow
    close_all_and_stop → additionally triggers real close-all + run stop
                         (the manager escalates to auto-demotion + audit)
"""

from __future__ import annotations

import uuid as uuid_mod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.engine.session import AsyncSessionLocal
from app.models import LiveRun, RiskRule

RULE_SCOPE_GLOBAL = "global"
RULE_SCOPE_ACCOUNT = "account"
RULE_SCOPE_RUN = "run"


@dataclass
class RiskVerdict:
    """Outcome of the pre-trade evaluation."""

    allow: bool
    error_code: str | None = None      # RISK_BLOCKED | INSUFFICIENT_MARGIN | ...
    error_message: str | None = None
    action: str | None = None          # reject | halt_open_today | close_all_and_stop
    rule_id: str | None = None
    rule_type: str | None = None
    threshold: float = 0.0
    observed: float = 0.0
    detail: dict = field(default_factory=dict)


def _f(v: object, default: float = 0.0) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _tomorrow() -> str:
    return (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%d")


def _same_direction(positions: list[dict], side: str) -> list[dict]:
    want = "buy" if side == "buy" else "sell"
    return [p for p in positions if str(p.get("type", "")) == want]


def evaluate(
    *,
    op: str,
    symbol: str,
    volume: float | None,
    position_id: str | None,
    run: dict,
    account: dict,
    positions: list[dict],
    rules: list[dict],
    symbol_info: dict | None = None,
    orders_this_minute: int = 0,
    recent_deals: list[dict] | None = None,
    closed_trades: list[dict] | None = None,
    registry_open_times: list[int] | None = None,
    order_source: str = "ai",
) -> RiskVerdict:
    """Evaluate one order against the resolved rule set.

    ``run``/``account``/``positions``/``rules`` are plain dicts so this stays
    DB-free and unit-testable.  ``rules`` must already be scoped/precedenced.
    ``recent_deals`` is the account's closed-deal history (ascending or
    unordered, gateway /trade/deals rows) — only needed when a
    ``consecutive_loss_cooldown`` rule is active; callers may omit it otherwise.
    ``order_source`` is "ai" (strategy auto-order) or "manual" (手动下单) —
    rules with ``apply_to`` set to the other source are skipped.
    """
    # 适用范围过滤：apply_to = ai | manual | both（默认 both，兼容旧规则）
    rules = [r for r in rules if str(r.get("apply_to") or "both") in ("both", order_source)]
    is_open = op in ("buy", "sell")
    equity = _f(account.get("equity"))
    balance = _f(account.get("balance"))
    margin_free = _f(account.get("margin_free"))
    today = _today()

    # ── 0. halt_open_today already active (persisted marker) ──
    if is_open and run.get("halt_until") and run["halt_until"] >= today:
        return RiskVerdict(
            allow=False, error_code="RISK_BLOCKED",
            error_message=f"当日已触发停止开仓（{run['halt_until']}），仅允许平仓",
            action="reject", rule_type="halt_open_today",
        )

    # ── 1. account frozen by reconciliation mismatch ──
    if run.get("account_frozen"):
        return RiskVerdict(
            allow=False, error_code="RISK_BLOCKED",
            error_message="账户因持仓对账不一致被冻结，禁止自动交易",
            action="reject", rule_type="reconcile_frozen",
        )

    # ── 2. observation ramp caps (PRD 阶梯放量) ──
    obs_raw = run.get("observation_ends_at")
    in_observation = obs_raw is not None and (
        (obs_raw if isinstance(obs_raw, datetime)
         else datetime.fromisoformat(str(obs_raw))) > datetime.now(UTC)
    )
    if in_observation and is_open:
        lot_cap = _f(run.get("lot_cap"), 0.0)
        if lot_cap > 0 and _f(volume) > lot_cap + 1e-9:
            return RiskVerdict(
                allow=False, error_code="RISK_BLOCKED",
                error_message=f"观察期单笔手数上限 {lot_cap}（触发风控将重置计时）",
                action="reject", rule_type="observation_lot_cap",
                threshold=lot_cap, observed=_f(volume),
            )
        cap_pct = _f(run.get("capital_cap_pct"), 100.0)
        allocated = _f(run.get("allocated_capital"))
        contract = _f((symbol_info or {}).get("contract_size"), 100000.0)
        price = _f((symbol_info or {}).get("ref_price")) or _ref_price(positions, symbol)
        notional = _f(volume) * contract * price
        same_notional = sum(
            _f(p.get("volume")) * contract * _f(p.get("price_current"))
            for p in _same_direction(positions, str(op))
        )
        cap = allocated * cap_pct / 100.0
        if price > 0 and same_notional + notional > cap + 1e-6:
            return RiskVerdict(
                allow=False, error_code="RISK_BLOCKED",
                error_message=f"观察期资金上限 {cap:.2f}（{cap_pct:.0f}%×分配资金）不足",
                action="reject", rule_type="observation_capital_cap",
                threshold=cap, observed=same_notional + notional,
            )

    # ── 3. per-order lot cap (rule) ──
    if is_open:
        for r in _typed(rules, "max_lot_per_order"):
            if _f(volume) > _f(r["threshold"]) + 1e-9:
                return _blocked(r, _f(volume))

    # ── 4. max open positions ──
    if is_open:
        for r in _typed(rules, "max_open_positions"):
            if len(positions) + 1 > _f(r["threshold"]) + 1e-9:
                return _blocked(r, float(len(positions) + 1))

    # ── 5. same-direction exposure ──
    if is_open:
        contract = _f((symbol_info or {}).get("contract_size"), 100000.0)
        price = _f((symbol_info or {}).get("ref_price")) or _ref_price(positions, symbol)
        for r in _typed(rules, "max_exposure"):
            cur = sum(
                _f(p.get("volume")) * contract * _f(p.get("price_current"))
                for p in _same_direction(positions, str(op))
            )
            add = _f(volume) * contract * price
            if price > 0 and cur + add > _f(r["threshold"]) + 1e-6:
                return _blocked(r, cur + add)

    # ── 6. order frequency (run-scoped, parent counts the last minute) ──
    if is_open:
        for r in _typed(rules, "max_orders_per_minute"):
            if orders_this_minute + 1 > _f(r["threshold"]) + 1e-9:
                return _blocked(r, float(orders_this_minute + 1))

    # ── 6.5 consecutive-loss cooldown（连续亏损 N 笔 → 暂停 M 分钟）──
    # 语义：从最近一笔平仓往前数，连续亏损达到 N 笔的时刻起休息 M 分钟；
    # 休息期内禁止开仓（平仓不受限）；休息结束后从零重新计数（期间盈利则立即复位）。
    # 数据源优先级：closed_trades（持仓登记表，按票号直查成交，可靠）>
    # recent_deals（终端按日期历史，依赖本地同步，可能为空）。
    cooldown_rules = _typed(rules, "consecutive_loss_cooldown")
    if is_open and cooldown_rules:
        if closed_trades is not None:
            closes = sorted(closed_trades, key=lambda d: int(_f(d.get("time"), 0)))
        elif recent_deals:
            closes = sorted(
                (
                    d for d in recent_deals
                    if int(_f(d.get("entry"), -1)) in (1, 3)  # DEAL_ENTRY_OUT / OUT_BY
                ),
                key=lambda d: int(_f(d.get("time"), 0)),
            )
        else:
            closes = []
        streak_times_desc: list[int] = []  # 连续亏损平仓时间，最新在前
        for d in reversed(closes):
            if _f(d.get("profit")) < 0:
                streak_times_desc.append(int(_f(d.get("time"), 0)))
            else:
                break
        now_ts = datetime.now(UTC).timestamp()
        for r in cooldown_rules:
            n = int(_f(r["threshold"]))
            minutes = _f(r.get("cooldown_minutes"), 0.0)
            if n <= 0 or minutes <= 0 or len(streak_times_desc) < n:
                continue
            triggered_ts = streak_times_desc[n - 1]  # 连续亏损达到 N 笔的时刻
            until_ts = triggered_ts + minutes * 60
            if now_ts < until_ts:
                remain_min = (until_ts - now_ts) / 60.0
                resume = datetime.fromtimestamp(until_ts, tz=UTC).strftime("%H:%M")
                return RiskVerdict(
                    allow=False, error_code="RISK_BLOCKED",
                    error_message=(
                        f"连续亏损 {n} 笔，暂停开仓休息 {minutes / 60:g} 小时"
                        f"（约剩 {max(1, round(remain_min))} 分钟，{resume}（UTC）后恢复）"
                    ),
                    action="reject", rule_id=str(r.get("id") or ""),
                    rule_type="consecutive_loss_cooldown",
                    threshold=float(n), observed=float(len(streak_times_desc)),
                    detail={"cooldown_minutes": minutes, "cooldown_until": until_ts,
                            "streak": len(streak_times_desc), "triggered_ts": triggered_ts},
                )

    # ── 6.6 时间窗口限频（W 分钟内最多下 N 单；平仓/部分平仓不计）──
    # 三计数源取最大值（避免同单双计，任一源可靠即可拦截）：
    #   a) recent_deals 的开仓成交（entry IN/INOUT）——依赖终端历史同步；
    #   b) 当前持仓的开仓时间——即时；
    #   c) registry_open_times（持仓登记表全部开仓时间）——含"开了又平"的。
    rate_rules = _typed(rules, "order_rate_window")
    if is_open and rate_rules:
        now_ts = datetime.now(UTC).timestamp()
        for r in rate_rules:
            max_n = int(_f(r["threshold"]))
            window_min = _f(r.get("window_minutes"), 0.0)
            if max_n <= 0 or window_min <= 0:
                continue
            cutoff = now_ts - window_min * 60
            deal_opens = [
                int(_f(d.get("time"), 0))
                for d in (recent_deals or [])
                if int(_f(d.get("entry"), -1)) in (0, 2)  # DEAL_ENTRY_IN / INOUT（开仓方向）
                and int(_f(d.get("time"), 0)) >= cutoff
            ]
            reg_opens = [t for t in (registry_open_times or []) if t >= cutoff]
            pos_opens = [
                int(_f(p.get("time"), 0))
                for p in (positions or [])
                if int(_f(p.get("time"), 0)) >= cutoff
            ]
            source_map = {
                "deal": len(deal_opens),
                "registry": len(reg_opens),
                "pos": len(pos_opens),
            }
            opens_in_window = max(source_map.values())
            if opens_in_window >= max_n:
                src_name = max(source_map, key=lambda k: source_map[k])
                source_times = {
                    "deal": deal_opens,
                    "registry": reg_opens,
                    "pos": pos_opens,
                }[src_name]
                resume_ts = min(source_times) + window_min * 60
                resume = datetime.fromtimestamp(resume_ts, tz=UTC).strftime("%H:%M")
                remain_min = max(0.0, (resume_ts - now_ts) / 60.0)
                return RiskVerdict(
                    allow=False, error_code="RISK_BLOCKED",
                    error_message=(
                        f"近 {window_min:g} 分钟内已开仓 {opens_in_window} 次（上限 {max_n} 次），"
                        f"暂停开仓至 {resume}（UTC，约剩 {max(1, round(remain_min))} 分钟）"
                    ),
                    action="reject", rule_id=str(r.get("id") or ""),
                    rule_type="order_rate_window",
                    threshold=float(max_n), observed=float(opens_in_window),
                    detail={"window_minutes": window_min,
                            "opens_in_window": opens_in_window,
                            "deal_opens": len(deal_opens),
                            "registry_opens": len(reg_opens),
                            "pos_opens": len(pos_opens),
                            "resume_ts": resume_ts},
                )

    # ── 7. daily max loss (absolute / pct of day-start equity) ──
    day_start = _f(run.get("day_start_equity")) or equity
    if day_start > 0:
        loss = max(0.0, day_start - equity)
        for r in _typed(rules, "daily_max_loss"):
            if loss >= _f(r["threshold"]) > 0:
                v = _blocked(r, loss)
                v.action = r.get("action") or "halt_open_today"
                return v
        for r in _typed(rules, "daily_max_loss_pct"):
            pct = loss / day_start * 100.0
            if pct >= _f(r["threshold"]) > 0:
                v = _blocked(r, pct)
                v.action = r.get("action") or "halt_open_today"
                return v

    # ── 8. max total drawdown vs peak ──
    peak = _f(run.get("peak_equity")) or equity
    if peak > 0 and balance >= 0:
        dd_pct = (peak - equity) / peak * 100.0
        for r in _typed(rules, "max_drawdown"):
            if dd_pct >= _f(r["threshold"]) > 0:
                v = _blocked(r, dd_pct)
                v.action = "close_all_and_stop"  # PRD: 全平仓+停止策略
                return v

    # ── 9. real free-margin check (broker truth, not the sim) ──
    if is_open and margin_free <= 0:
        return RiskVerdict(
            allow=False, error_code="INSUFFICIENT_MARGIN",
            error_message=f"可用保证金不足（{margin_free:.2f}）",
            action="reject", rule_type="free_margin",
        )

    return RiskVerdict(allow=True)


def _typed(rules: list[dict], type_: str) -> list[dict]:
    return [r for r in rules if r.get("type") == type_ and r.get("enabled", True)]


def _blocked(r: dict, observed: float) -> RiskVerdict:
    return RiskVerdict(
        allow=False, error_code="RISK_BLOCKED",
        error_message=f"风控拦截 [{r['type']}] 阈值 {r['threshold']}，当前 {observed:.4f}",
        action=r.get("action") or "reject",
        rule_id=str(r.get("id") or ""), rule_type=str(r["type"]),
        threshold=_f(r.get("threshold")), observed=observed,
    )


def _ref_price(positions: list[dict], symbol: str) -> float:
    for p in positions:
        if str(p.get("symbol", "")).upper() == symbol.upper():
            return _f(p.get("price_current"))
    return 0.0


async def load_rules(run_id: str | None, account_id: str) -> list[dict]:
    """Resolve the active rule set for one order: run > account > global.

    ``run_id`` is optional (M14 manual trading has no live run): when None the
    run-scoped rules are skipped and only account + global rules resolve.
    """
    scopes = [
        (RiskRule.scope == RULE_SCOPE_ACCOUNT) & (RiskRule.account_id == account_id),
        RiskRule.scope == RULE_SCOPE_GLOBAL,
    ]
    if run_id is not None:
        scopes.append((RiskRule.scope == RULE_SCOPE_RUN) & (RiskRule.run_id == run_id))
    cond = scopes[0]
    for c in scopes[1:]:
        cond = cond | c
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(RiskRule).where(
                RiskRule.enabled.is_(True), cond
            )
        )).scalars().all()
    by_type: dict[str, dict] = {}
    precedence = {RULE_SCOPE_RUN: 3, RULE_SCOPE_ACCOUNT: 2, RULE_SCOPE_GLOBAL: 1}
    for r in rows:
        d = {
            "id": str(r.id), "type": r.type, "threshold": float(r.threshold),
            "cooldown_minutes": r.cooldown_minutes, "window_minutes": r.window_minutes,
            "apply_to": r.apply_to or "both",
            "action": r.action, "enabled": r.enabled, "scope": r.scope,
        }
        cur = by_type.get(r.type)
        if cur is None or precedence[r.scope] > precedence[cur["scope"]]:
            by_type[r.type] = d
    return list(by_type.values())


async def day_start_equity(run_id: str, current_equity: float) -> tuple[float, str]:
    """Return (day_start_equity, halt_until) rolling the UTC day if needed.

    Called by the manager whenever it writes the account mirror; the returned
    values are persisted back onto the run row.
    """
    today = _today()
    async with AsyncSessionLocal() as db:
        run = await db.get(LiveRun, uuid_mod.UUID(run_id))
        if run is None:
            return current_equity, ""
        if run.day_start_date != today or run.day_start_equity == Decimal("0"):
            run.day_start_date = today
            run.day_start_equity = Decimal(str(current_equity))
            db.add(run)
            await db.commit()
        halt = run.halt_until if run.halt_until and run.halt_until >= today else ""
        return float(run.day_start_equity), halt
