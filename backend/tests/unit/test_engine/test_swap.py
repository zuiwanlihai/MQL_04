"""Overnight swap accrual (PRD M4 P1 隔夜利息) — engine-level tests.

Convention: one night of swap per new UTC day rollover, Wednesday ×3
(weekend coverage), Saturday/Sunday rollovers skipped.  Cost formula:
swap_points × point × contract_size × volume.  Differences are asserted
against a swap-free baseline run to stay independent of fill pricing.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from app.sandbox.engine import BacktestEngine
from app.sandbox.import_hook import uninstall_all
from tests.unit.bt_helpers import SYMBOL_INFO, make_payload

POINT = 0.00001
CONTRACT = float(SYMBOL_INFO["contract_size"])
VOL = 0.1
# Monday 2026-03-02 00:00 UTC — deterministic week anchor.
MON0 = int(datetime(2026, 3, 2, tzinfo=UTC).timestamp())
assert datetime.fromtimestamp(MON0, tz=UTC).weekday() == 0


@pytest.fixture(autouse=True)
def _clean_sandbox():
    yield
    uninstall_all()


def hour_bars(n: int) -> "object":
    times = MON0 + np.arange(n, dtype="int64") * 3600
    closes = 1.10000 + np.arange(n) * 0.00005
    opens = closes - 0.00001
    highs = np.maximum(opens, closes) + 0.00002
    lows = np.minimum(opens, closes) - 0.00002
    from app.sandbox.payload import BarArrays

    return BarArrays(
        time=times,
        open=opens,
        high=highs,
        low=lows,
        close=closes,
        tick_volume=np.full(n, 100, dtype="int64"),
        spread=np.full(n, 20, dtype="int64"),
    )


HOLD_WEEK = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_bar(ctx, bar):
    if ctx.storage.get("done"):
        return
    ctx.storage["done"] = True
    ctx.buy("EURUSD", 0.1)

def on_order(ctx, order):
    pass
"""


def _run(source: str, bars, **kw):
    payload = make_payload(source, mode="standard", warmup=0, **kw)
    engine = BacktestEngine(payload, bars)
    events = list(engine.run())
    done = [e for e in events if e.kind == "done"]
    trades = [e.data for e in events if e.kind == "trade"]
    assert done, "engine must complete"
    return done[0].data["report"], trades


# 192 hourly bars: Monday 00:00 → next Monday 23:00 (full week).
BARS_WEEK = hour_bars(192)


def test_swap_one_night_per_weekday_wednesday_triple():
    """7 nights per held week: Tue1 + Wed3 + Thu1 + Fri1 + Mon1, weekend skipped."""
    baseline, _ = _run(HOLD_WEEK, BARS_WEEK)
    report, trades = _run(
        HOLD_WEEK, BARS_WEEK, swap_long=-1.0, swap_short=-1.0
    )
    assert len(trades) == 1
    # -1 point × 0.00001 × 100000 × 0.1 = -$0.10 per night
    assert trades[0]["swap"] == pytest.approx(-0.7)
    assert report["total_swap"] == pytest.approx(-0.7)
    # profit is net of swap; balance reconciles
    assert report["final_balance"] == pytest.approx(baseline["final_balance"] - 0.7)
    assert report["total_return"] == pytest.approx(baseline["total_return"] - 0.7)


def test_swap_uses_short_rate_for_sells():
    src = HOLD_WEEK.replace("ctx.buy", "ctx.sell")
    report, trades = _run(src, BARS_WEEK, swap_long=0.0, swap_short=-2.0)
    assert len(trades) == 1
    assert trades[0]["swap"] == pytest.approx(-1.4)  # 7 nights × $0.20


def test_swap_zero_rate_matches_baseline():
    baseline, _ = _run(HOLD_WEEK, BARS_WEEK)
    report, trades = _run(HOLD_WEEK, BARS_WEEK, swap_long=0.0, swap_short=0.0)
    assert trades[0]["swap"] == 0.0
    assert report["final_balance"] == pytest.approx(baseline["final_balance"])


def test_positive_swap_credits_balance():
    baseline, _ = _run(HOLD_WEEK, BARS_WEEK)
    report, trades = _run(HOLD_WEEK, BARS_WEEK, swap_long=0.5, swap_short=0.5)
    assert trades[0]["swap"] == pytest.approx(0.35)  # 7 × +$0.05
    assert report["final_balance"] == pytest.approx(baseline["final_balance"] + 0.35)


def test_open_position_snapshot_carries_accrued_swap():
    """Position exposes the accrual via get_positions(): close once swap != 0."""
    src = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_bar(ctx, bar):
    if not ctx.storage.get("done"):
        ctx.storage["done"] = True
        ctx.buy("EURUSD", 0.1)
        return
    positions = ctx.get_positions()
    if positions and positions[0].swap != 0.0:
        ctx.close(positions[0].id)

def on_order(ctx, order):
    pass
"""
    payload = make_payload(src, mode="standard", warmup=0, swap_long=-1.0)
    engine = BacktestEngine(payload, hour_bars(48))  # 2 days
    events = list(engine.run())
    trades = [e.data for e in events if e.kind == "trade"]
    assert trades and trades[0]["swap"] == pytest.approx(-0.1)


def test_swap_charged_before_opening_bar_position():
    """A position opened during the first bar of a new day is not charged
    for that day's night: opening Monday 00:00 → first charge Tuesday."""
    two_day = hour_bars(48)  # Mon 00:00 → Tue 23:00
    report, trades = _run(HOLD_WEEK, two_day, swap_long=-1.0)
    assert trades[0]["swap"] == pytest.approx(-0.1)  # only Tuesday rollover
    assert report["total_swap"] == pytest.approx(-0.1)
