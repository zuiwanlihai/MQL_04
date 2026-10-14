"""FillModel three-tier price formulas + AccountSim margin/volume/stop-out."""

from __future__ import annotations

import pytest

from app.sandbox.matching import (
    CONSERVATIVE,
    OPTIMISTIC,
    STANDARD,
    AccountSim,
    Fill,
    FillModel,
    MatchResult,
)

POINT = 0.00001


def _model(mode: str, spread_mode: str = "current", fixed: float = 0.0,
           snapshot: float = 20.0, slip: float | None = None) -> FillModel:
    defaults = {"conservative": 2.0, "standard": 1.0, "optimistic": 0.0}
    slippage = defaults[mode] if slip is None else slip
    return FillModel(mode, POINT, spread_mode, fixed, snapshot, slippage)


# ── spread_points_at ───────────────────────────────────────────

def test_spread_points_optimistic_always_zero():
    assert _model(OPTIMISTIC).spread_points_at(30) == 0.0


def test_spread_points_fixed_mode():
    m = _model(STANDARD, spread_mode="fixed", fixed=15.0)
    assert m.spread_points_at(999) == 15.0


def test_spread_points_current_uses_bar_then_snapshot():
    m = _model(STANDARD)
    assert m.spread_points_at(25) == 25.0
    assert m.spread_points_at(0) == 20.0  # bar spread 0 → snapshot fallback


# ── conservative tier ──────────────────────────────────────────

def test_conservative_buy_next_open_plus_spread_plus_slip():
    m = _model(CONSERVATIVE)
    r = m.fill_for("buy", cur_close=1.10000, next_open=1.10050,
                   next_spread_pts=20, cur_spread_pts=30)
    assert r.success and r.fill is not None
    assert r.fill.base_price == pytest.approx(1.10050)
    assert r.fill.price == pytest.approx(1.10050 + 20 * POINT + 2 * POINT)
    assert r.fill.spread_cost == pytest.approx(20 * POINT)
    assert r.fill.slippage_cost == pytest.approx(2 * POINT)


def test_conservative_sell_next_open_minus_slip_no_spread():
    m = _model(CONSERVATIVE)
    r = m.fill_for("sell", cur_close=1.10000, next_open=1.10050,
                   next_spread_pts=20, cur_spread_pts=30)
    assert r.fill.price == pytest.approx(1.10050 - 2 * POINT)
    assert r.fill.spread_cost == 0.0


def test_conservative_last_bar_market_closed():
    m = _model(CONSERVATIVE)
    r = m.fill_for("buy", 1.10000, None, 0, 20)
    assert not r.success
    assert r.error_code == "MARKET_CLOSED"  # ErrorCode is a str enum


# ── standard tier ──────────────────────────────────────────────

def test_standard_buy_close_plus_costs():
    m = _model(STANDARD)
    r = m.fill_for("buy", 1.10000, None, 0, 20)
    assert r.fill.price == pytest.approx(1.10000 + 20 * POINT + 1 * POINT)
    assert r.fill.base_price == pytest.approx(1.10000)


def test_standard_sell_close_minus_slip():
    m = _model(STANDARD)
    r = m.fill_for("sell", 1.10000, None, 0, 20)
    assert r.fill.price == pytest.approx(1.10000 - 1 * POINT)
    assert r.fill.spread_cost == 0.0


# ── optimistic tier ────────────────────────────────────────────

def test_optimistic_close_zero_costs():
    m = _model(OPTIMISTIC)
    r = m.fill_for("buy", 1.10000, None, 0, 20)
    assert r.fill.price == pytest.approx(1.10000)
    assert r.fill.spread_cost == 0.0 and r.fill.slippage_cost == 0.0
    r2 = m.fill_for("sell", 1.10000, None, 0, 20)
    assert r2.fill.price == pytest.approx(1.10000)


# ── AccountSim ─────────────────────────────────────────────────

def _account(**kw) -> AccountSim:
    base = {
        "initial_deposit": 10000.0, "leverage": 100, "contract_size": 100000.0,
        "point": POINT, "volume_step": 0.01, "volume_min": 0.01,
        "volume_max": 100.0, "commission_per_lot": 7.0, "stop_out_level": 50.0,
    }
    base.update(kw)
    return AccountSim(**base)


def test_margin_for_formula():
    acc = _account()
    assert acc.margin_for(0.1, 1.10000) == pytest.approx(110.0)


def test_normalize_volume_floor_to_step():
    acc = _account()
    vol, err = acc.normalize_volume(0.125)
    assert err is None and vol == pytest.approx(0.12)


def test_normalize_volume_below_min_rejected():
    acc = _account()
    vol, err = acc.normalize_volume(0.005)
    assert err is not None and err.error_code == "INVALID_VOLUME"


def test_normalize_volume_above_max_rejected():
    acc = _account()
    _, err = acc.normalize_volume(150.0)
    assert err is not None and err.error_code == "INVALID_VOLUME"


def test_open_close_round_trip_pnl_and_commission():
    acc = _account()
    open_fill = Fill(price=1.10000, base_price=1.10000,
                     spread_cost=0.0002, slippage_cost=0.0)
    res = acc.open_position("buy", open_fill, 0.1, 1760000000, "")
    pos_id = res.position_id
    close_fill = Fill(price=1.10100, base_price=1.10100,
                      spread_cost=0.0, slippage_cost=0.0)
    out = acc.close_position(pos_id, close_fill, 1760003600)
    trade = out.trade
    # pnl = (1.10100 - 1.10000) * 100000 * 0.1 = 10; commission = 0.7
    assert trade["profit"] == pytest.approx(9.3)
    assert trade["commission"] == pytest.approx(0.7)
    assert trade["spread_cost"] == pytest.approx(0.0002)
    assert acc.balance == pytest.approx(10009.3)
    assert not acc.positions


def test_close_unknown_position():
    acc = _account()
    fill = Fill(1.1, 1.1, 0.0, 0.0)
    out = acc.close_position("999", fill, 1760000000)
    assert not out.success and out.error_code == "POSITION_NOT_FOUND"


def test_hedging_positions_coexist():
    acc = _account()
    f = Fill(1.1, 1.1, 0.0, 0.0)
    acc.open_position("buy", f, 0.1, 1760000000, "")
    acc.open_position("sell", f, 0.1, 1760000000, "")
    assert len(acc.positions) == 2


def test_unrealized_and_equity():
    acc = _account()
    f = Fill(price=1.10000, base_price=1.10000, spread_cost=0.0, slippage_cost=0.0)
    acc.open_position("buy", f, 0.1, 1760000000, "")
    assert acc.unrealized_pnl(1.10100) == pytest.approx(10.0)
    assert acc.equity(1.10100) == pytest.approx(10010.0)


def test_stop_out_forces_biggest_loser():
    acc = _account(initial_deposit=1000.0, commission_per_lot=0.0)
    f = Fill(price=1.10000, base_price=1.10000, spread_cost=0.0, slippage_cost=0.0)
    acc.open_position("buy", f, 1.0, 1760000000, "")
    bid = 1.09350  # unrealized -650 → equity 350, used ~1093.5 → level ~32%
    assert acc.margin_level(bid) < 50.0
    forced = acc.check_stop_out(
        bid, 1760003600,
        lambda side: MatchResult(success=True, fill=Fill(bid, bid, 0.0, 0.0)),
    )
    assert len(forced) == 1
    assert forced[0].trade["is_forced"] is True
    assert forced[0].trade["comment"] == "stop-out"
    assert acc.balance == pytest.approx(350.0)
    assert not acc.positions


def test_force_close_all_end_of_run():
    acc = _account(commission_per_lot=0.0)
    f = Fill(price=1.10000, base_price=1.10000, spread_cost=0.0, slippage_cost=0.0)
    acc.open_position("buy", f, 0.1, 1760000000, "")
    acc.open_position("sell", f, 0.1, 1760000000, "")
    out = acc.force_close_all(
        1760099999,
        lambda side: MatchResult(success=True, fill=Fill(1.10000, 1.10000, 0.0, 0.0)),
    )
    assert len(out) == 2
    assert all(r.trade["comment"] == "end-of-run" for r in out)
    assert not acc.positions
