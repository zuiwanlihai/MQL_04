"""BacktestEngine replay: order gates, tiers, truncation, determinism, forced close."""

from __future__ import annotations

import pytest

from app.sandbox.engine import BacktestEngine
from app.sandbox.import_hook import uninstall_all
from tests.unit.bt_helpers import BUY_ONCE, T0, make_bars, make_payload


@pytest.fixture(autouse=True)
def _clean_sandbox():
    yield
    uninstall_all()  # engine.run() installs meta_path finder + module views


def _run(source: str, mode: str = "standard", bars=None, **kw):
    bars = bars if bars is not None else make_bars(12)
    payload = make_payload(source, mode=mode, **kw)
    engine = BacktestEngine(payload, bars)
    events = list(engine.run())
    return events


def _by_kind(events, kind):
    return [e for e in events if e.kind == kind]


def _log_messages(events):
    return [e.data["message"] for e in _by_kind(events, "log") if e.data["kind"] == "log"]


# ── lifecycle failures ─────────────────────────────────────────

def test_missing_declare_fails():
    src = """
def on_init(ctx):
    pass
def on_bar(ctx, bar):
    pass
def on_order(ctx, order):
    pass
"""
    events = _run(src)
    fails = _by_kind(events, "fail")
    assert len(fails) == 1
    assert "declare" in fails[0].data["message"]
    assert not _by_kind(events, "done")


def test_missing_callback_load_failure():
    src = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")
def on_bar(ctx, bar):
    pass
"""
    events = _run(src)
    fails = _by_kind(events, "fail")
    assert fails and fails[0].data.get("load_failed") is True
    assert "on_order" in fails[0].data["message"]


def test_empty_bars_fails():
    import numpy as np

    from app.sandbox.payload import BarArrays

    empty = BarArrays(
        time=np.zeros(0, dtype="int64"), open=np.zeros(0), high=np.zeros(0),
        low=np.zeros(0), close=np.zeros(0), tick_volume=np.zeros(0, dtype="int64"),
        spread=np.zeros(0, dtype="int64"),
    )
    events = _run(BUY_ONCE, bars=empty)
    fails = _by_kind(events, "fail")
    assert fails and "没有 K 线" in fails[0].data["message"]


# ── order gates ────────────────────────────────────────────────

def test_warmup_rejects_then_allows():
    src = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    if bar.time.timestamp() == %d:
        r = ctx.buy("EURUSD", 0.1)
        ctx.log.info(f"W0:{r.error_code}")
    if bar.time.timestamp() == %d:
        r = ctx.buy("EURUSD", 0.1)
        ctx.log.info(f"W1:{r.success}")

def on_order(ctx, order):
    pass
""" % (T0, T0 + 3600)
    events = _run(src, warmup=1)
    msgs = _log_messages(events)
    assert "W0:ErrorCode.WARMUP_PERIOD" in msgs or any(
        m.startswith("W0:") and "WARMUP_PERIOD" in m for m in msgs
    )
    assert any(m.startswith("W1:True") for m in msgs)


def test_order_in_on_init_rejected_stopped():
    src = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")
    r = ctx.buy("EURUSD", 0.1)
    ctx.log.info(f"INIT:{r.error_code}")

def on_bar(ctx, bar):
    pass
def on_order(ctx, order):
    pass
"""
    events = _run(src)
    assert any("STRATEGY_STOPPED" in m for m in _log_messages(events))


def test_rate_limited_per_bar():
    src = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    if ctx.storage.get("fired"):
        return
    ctx.storage["fired"] = True
    codes = []
    for i in range(25):
        r = ctx.buy("EURUSD", 0.01)
        codes.append(str(r.error_code))
    ctx.log.info("RL:" + ",".join(codes[19:22]))

def on_order(ctx, order):
    pass
"""
    events = _run(src)
    joined = " ".join(_log_messages(events))
    assert "RATE_LIMITED" in joined


def test_undeclared_symbol_rejected():
    src = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    if ctx.storage.get("fired"):
        return
    ctx.storage["fired"] = True
    r = ctx.buy("GBPUSD", 0.1)
    ctx.log.info(f"SYM:{r.error_code}")

def on_order(ctx, order):
    pass
"""
    events = _run(src)
    assert any("INVALID_SYMBOL" in m for m in _log_messages(events))


# ── get_bars no-lookahead ──────────────────────────────────────

def test_get_bars_truncated_to_current_index():
    src = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    if ctx.storage.get("n") is None:
        ctx.storage["n"] = len(ctx.get_bars("EURUSD", "H1", 1000))
        ctx.log.info(f"NB:{ctx.storage['n']}")

def on_order(ctx, order):
    pass
"""
    events = _run(src)
    assert any("NB:1" in m for m in _log_messages(events))


def test_get_bars_other_symbol_empty():
    src = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    if ctx.storage.get("fired"):
        return
    ctx.storage["fired"] = True
    ctx.log.info(f"GB:{len(ctx.get_bars('XAUUSD', 'H1', 5))}")

def on_order(ctx, order):
    pass
"""
    events = _run(src)
    assert any("GB:0" in m for m in _log_messages(events))


# ── fill prices per tier ───────────────────────────────────────

def _buy_and_close_source() -> str:
    return """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    i = int((bar.time.timestamp() - %d) / 3600)
    if i == 1 and not ctx.storage.get("opened"):
        ctx.storage["opened"] = True
        r = ctx.buy("EURUSD", 0.1)
        ctx.storage["open_px"] = r.filled_price
    if i == 3:
        pid = ctx.get_positions()[0].id
        r = ctx.close(pid)
        ctx.storage["close_px"] = r.filled_price

def on_order(ctx, order):
    pass
""" % T0


def test_standard_tier_prices():
    bars = make_bars(12)
    events = _run(_buy_and_close_source(), mode="standard", bars=bars)
    trades = _by_kind(events, "trade")
    assert len(trades) == 1
    t = trades[0].data
    point = 0.00001
    # buy at bar1 close + 20pt spread + 1pt slip; sell-close at bar3 close - 1pt
    assert t["open_price"] == pytest.approx(float(bars.close[1]) + 21 * point)
    assert t["close_price"] == pytest.approx(float(bars.close[3]) - 1 * point)
    assert t["spread_cost"] == pytest.approx(20 * point)


def test_conservative_tier_uses_next_open():
    bars = make_bars(12)
    events = _run(_buy_and_close_source(), mode="conservative", bars=bars)
    trades = _by_kind(events, "trade")
    assert len(trades) == 1
    t = trades[0].data
    point = 0.00001
    assert t["open_price"] == pytest.approx(float(bars.open[2]) + 22 * point)
    assert t["close_price"] == pytest.approx(float(bars.open[4]) - 2 * point)
    assert t["base_price"] == pytest.approx(float(bars.open[2]))


def test_optimistic_tier_close_price():
    bars = make_bars(12)
    events = _run(_buy_and_close_source(), mode="optimistic", bars=bars)
    t = _by_kind(events, "trade")[0].data
    assert t["open_price"] == pytest.approx(float(bars.close[1]))
    assert t["close_price"] == pytest.approx(float(bars.close[3]))
    assert t["spread_cost"] == 0.0 and t["slippage_cost"] == 0.0


def test_conservative_last_bar_market_closed():
    src = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    if int((bar.time.timestamp() - %d) / 3600) == 2:
        r = ctx.buy("EURUSD", 0.1)
        ctx.log.info(f"MC:{r.error_code}")

def on_order(ctx, order):
    pass
""" % T0
    events = _run(src, mode="conservative", bars=make_bars(3))
    assert any("MARKET_CLOSED" in m for m in _log_messages(events))


# ── margin / forced close / determinism ────────────────────────

def test_insufficient_margin_business_error():
    src = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    if ctx.storage.get("fired"):
        return
    ctx.storage["fired"] = True
    r = ctx.buy("EURUSD", 50.0)
    ctx.log.info(f"IM:{r.error_code}")

def on_order(ctx, order):
    pass
"""
    events = _run(src, initial_deposit=1000.0)
    assert any("INSUFFICIENT_MARGIN" in m for m in _log_messages(events))


def test_end_of_run_forced_close_and_done_report():
    events = _run(BUY_ONCE, mode="standard")
    trades = _by_kind(events, "trade")
    assert len(trades) == 1  # forced close at final bar
    t = trades[0].data
    assert t["is_forced"] is True
    assert t["comment"] == "end-of-run"
    done = _by_kind(events, "done")
    assert len(done) == 1
    report = done[0].data["report"]
    assert report["n_bars"] == 12
    assert report["total_trades"] == 1
    curve = report["equity_curve"]
    assert curve and set(curve[0]) == {
        "time", "equity", "balance", "drawdown", "baseline"
    }
    # buy @ bar1 close+21pt, forced @ bar11 close-1pt on a rising series → profit
    assert report["total_return"] != 0.0


def test_determinism_same_inputs_same_report():
    e1 = _by_kind(_run(BUY_ONCE, mode="standard", warmup=1), "done")[0].data
    e2 = _by_kind(_run(BUY_ONCE, mode="standard", warmup=1), "done")[0].data
    assert e1["report"] == e2["report"]


def test_progress_events_monotonic():
    events = _run(BUY_ONCE)
    pcts = [e.data["percent"] for e in _by_kind(events, "progress")]
    assert pcts == sorted(pcts)
    assert pcts[-1] == 100
