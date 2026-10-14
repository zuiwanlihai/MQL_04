"""Pre-trade risk evaluator (M6 Phase C) — pure-function unit tests.

``evaluate`` runs in the parent before any gateway call; these pin the rule
semantics (reject / halt / close_all) and the observation ramp caps without a
DB or a live broker.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.risk.engine import evaluate

SYM = {"contract_size": 100000.0, "ref_price": 1.10000}


def _run(**kw) -> dict:
    base = {
        "allocated_capital": 1000.0, "capital_cap_pct": 100, "lot_cap": None,
        "observation_ends_at": None, "halt_until": "", "day_start_equity": 1000.0,
        "peak_equity": 1000.0, "account_frozen": False,
    }
    base.update(kw)
    return base


def _acc(equity=1000.0, balance=1000.0, margin_free=900.0) -> dict:
    return {"equity": equity, "balance": balance, "margin_free": margin_free}


def _rule(type_, threshold, action="reject", enabled=True):
    return {"id": f"{type_}-1", "type": type_, "threshold": threshold,
            "action": action, "enabled": enabled}


# ── pass-through ───────────────────────────────────────────────

def test_no_rules_allows_open():
    v = evaluate(op="buy", symbol="EURUSD", volume=0.1, position_id=None,
                 run=_run(), account=_acc(), positions=[], rules=[],
                 symbol_info=SYM)
    assert v.allow is True


def test_close_always_allowed_even_when_halted():
    v = evaluate(op="close", symbol="EURUSD", volume=0.1, position_id="1",
                 run=_run(halt_until=datetime.now(UTC).strftime("%Y-%m-%d")),
                 account=_acc(), positions=[], rules=[], symbol_info=SYM)
    assert v.allow is True


# ── account frozen / halt ──────────────────────────────────────

def test_frozen_account_blocks():
    v = evaluate(op="buy", symbol="EURUSD", volume=0.1, position_id=None,
                 run=_run(account_frozen=True), account=_acc(), positions=[],
                 rules=[], symbol_info=SYM)
    assert not v.allow and v.error_code == "RISK_BLOCKED"


def test_halt_open_today_blocks_new_orders():
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    v = evaluate(op="buy", symbol="EURUSD", volume=0.1, position_id=None,
                 run=_run(halt_until=today), account=_acc(), positions=[],
                 rules=[], symbol_info=SYM)
    assert not v.allow and "停止开仓" in v.error_message


def test_halt_expires_next_day():
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    v = evaluate(op="buy", symbol="EURUSD", volume=0.1, position_id=None,
                 run=_run(halt_until=yesterday), account=_acc(), positions=[],
                 rules=[], symbol_info=SYM)
    assert v.allow is True


# ── per-order lot cap ──────────────────────────────────────────

def test_max_lot_per_order_rejects():
    v = evaluate(op="buy", symbol="EURUSD", volume=0.5, position_id=None,
                 run=_run(), account=_acc(), positions=[],
                 rules=[_rule("max_lot_per_order", 0.2)], symbol_info=SYM)
    assert not v.allow and v.rule_type == "max_lot_per_order"


# ── open positions / exposure / frequency ──────────────────────

def test_max_open_positions_rejects():
    pos = [{"ticket": i, "symbol": "EURUSD", "type": "buy", "volume": 0.1,
            "price_current": 1.1} for i in range(3)]
    v = evaluate(op="buy", symbol="EURUSD", volume=0.1, position_id=None,
                 run=_run(), account=_acc(), positions=pos,
                 rules=[_rule("max_open_positions", 3)], symbol_info=SYM)
    assert not v.allow and v.rule_type == "max_open_positions"


def test_max_exposure_same_direction_only():
    pos = [{"ticket": 1, "symbol": "EURUSD", "type": "buy", "volume": 0.5,
            "price_current": 1.1}]  # 0.5×100000×1.1 = 55000 notional
    v = evaluate(op="buy", symbol="EURUSD", volume=0.1, position_id=None,
                 run=_run(), account=_acc(), positions=pos,
                 rules=[_rule("max_exposure", 60000)], symbol_info=SYM)
    assert not v.allow  # 55000 + 11000 > 60000
    # a sell is opposite direction → allowed
    v2 = evaluate(op="sell", symbol="EURUSD", volume=0.1, position_id=None,
                  run=_run(), account=_acc(), positions=pos,
                  rules=[_rule("max_exposure", 60000)], symbol_info=SYM)
    assert v2.allow


def test_frequency_limit():
    v = evaluate(op="buy", symbol="EURUSD", volume=0.1, position_id=None,
                 run=_run(), account=_acc(), positions=[],
                 rules=[_rule("max_orders_per_minute", 20)],
                 symbol_info=SYM, orders_this_minute=20)
    assert not v.allow and v.rule_type == "max_orders_per_minute"


def test_unknown_order_count_fails_closed():
    """计数取不到时必须拒绝：「不知道」不等于「这一分钟一单没有」。

    手动链路的前置计数来自审计日志查询，DB 抖一下就会失败。旧实现把失败折算
    成 0，于是限频规则在最需要它的时候恰好不生效——失效方向反了。
    """
    v = evaluate(op="buy", symbol="EURUSD", volume=0.1, position_id=None,
                 run=_run(), account=_acc(), positions=[],
                 rules=[_rule("max_orders_per_minute", 20)],
                 symbol_info=SYM, orders_this_minute=None)
    assert not v.allow
    assert v.error_code == "RISK_CHECK_UNAVAILABLE", v.error_code
    assert v.rule_type == "max_orders_per_minute"
    assert "无法" in v.error_message, v.error_message


def test_unknown_order_count_without_the_rule_stays_allowed():
    """没有规则消费这个计数时不得凭空拦截：那会是一次无谓的误拒。"""
    v = evaluate(op="buy", symbol="EURUSD", volume=0.1, position_id=None,
                 run=_run(), account=_acc(), positions=[],
                 rules=[_rule("max_lot_per_order", 1.0)],
                 symbol_info=SYM, orders_this_minute=None)
    assert v.allow is True


# ── daily loss / drawdown ──────────────────────────────────────

def test_daily_max_loss_absolute_halts():
    v = evaluate(op="buy", symbol="EURUSD", volume=0.1, position_id=None,
                 run=_run(day_start_equity=1000.0), account=_acc(equity=900.0),
                 positions=[], rules=[_rule("daily_max_loss", 50.0,
                                            action="halt_open_today")],
                 symbol_info=SYM)
    assert not v.allow and v.action == "halt_open_today"


def test_daily_max_loss_pct():
    v = evaluate(op="buy", symbol="EURUSD", volume=0.1, position_id=None,
                 run=_run(day_start_equity=1000.0), account=_acc(equity=940.0),
                 positions=[], rules=[_rule("daily_max_loss_pct", 5.0)],
                 symbol_info=SYM)
    assert not v.allow  # 6% loss ≥ 5%


def test_max_drawdown_triggers_close_all():
    v = evaluate(op="buy", symbol="EURUSD", volume=0.1, position_id=None,
                 run=_run(peak_equity=1000.0), account=_acc(equity=800.0),
                 positions=[], rules=[_rule("max_drawdown", 15.0)],
                 symbol_info=SYM)
    assert not v.allow and v.action == "close_all_and_stop"


# ── insufficient real margin ───────────────────────────────────

def test_zero_free_margin_blocks():
    v = evaluate(op="buy", symbol="EURUSD", volume=0.1, position_id=None,
                 run=_run(), account=_acc(margin_free=0.0), positions=[],
                 rules=[], symbol_info=SYM)
    assert not v.allow and v.error_code == "INSUFFICIENT_MARGIN"


# ── observation ramp caps ──────────────────────────────────────

def test_observation_lot_cap():
    future = datetime.now(UTC) + timedelta(days=3)
    v = evaluate(op="buy", symbol="EURUSD", volume=0.02, position_id=None,
                 run=_run(observation_ends_at=future, lot_cap=0.01),
                 account=_acc(), positions=[], rules=[], symbol_info=SYM)
    assert not v.allow and v.rule_type == "observation_lot_cap"


def test_observation_capital_cap():
    future = datetime.now(UTC) + timedelta(days=3)
    # allocated 1000 × 10% = 100 cap; 0.1 lot × 100000 × 1.1 = 11000 notional
    v = evaluate(op="buy", symbol="EURUSD", volume=0.1, position_id=None,
                 run=_run(observation_ends_at=future, capital_cap_pct=10),
                 account=_acc(), positions=[], rules=[], symbol_info=SYM)
    assert not v.allow and v.rule_type == "observation_capital_cap"


def test_observation_expired_no_caps():
    past = datetime.now(UTC) - timedelta(days=1)
    v = evaluate(op="buy", symbol="EURUSD", volume=0.5, position_id=None,
                 run=_run(observation_ends_at=past, lot_cap=0.01,
                          capital_cap_pct=10),
                 account=_acc(), positions=[], rules=[], symbol_info=SYM)
    assert v.allow


# ── disabled rules ignored ─────────────────────────────────────

def test_disabled_rule_ignored():
    v = evaluate(op="buy", symbol="EURUSD", volume=0.5, position_id=None,
                 run=_run(), account=_acc(), positions=[],
                 rules=[_rule("max_lot_per_order", 0.2, enabled=False)],
                 symbol_info=SYM)
    assert v.allow
