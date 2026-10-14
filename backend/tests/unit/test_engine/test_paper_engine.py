"""PaperEngine incremental loop: replay, feed, dedup, stop-close, storage."""

from __future__ import annotations

import json

import pytest

from app.sandbox.import_hook import uninstall_all
from app.sandbox.paper_engine import PaperEngine
from tests.unit.bt_helpers import BUY_ONCE, T0, make_bars, make_payload


@pytest.fixture(autouse=True)
def _clean_sandbox():
    yield
    uninstall_all()  # engine installs meta_path finder + module views


def _by_kind(events, kind):
    return [e for e in events if e.kind == kind]


def _log_messages(events):
    return [e.data["message"] for e in _by_kind(events, "log") if e.data["kind"] == "log"]


def _rows(bars, start_idx: int, n: int) -> list[list[float]]:
    """Convert bars[start_idx:start_idx+n] into feed() row format."""
    out = []
    for i in range(start_idx, start_idx + n):
        out.append([
            int(bars.time[i]),
            float(bars.open[i]),
            float(bars.high[i]),
            float(bars.low[i]),
            float(bars.close[i]),
            int(bars.tick_volume[i]),
            int(bars.spread[i]),
        ])
    return out


# ── start replay ───────────────────────────────────────────────

def test_start_replays_history_and_emits_account(tmp_path):
    payload = make_payload(BUY_ONCE, mode="standard", warmup=0)
    eng = PaperEngine(payload, make_bars(6), storage_path=tmp_path / "storage.json")
    events = list(eng.start())
    assert not _by_kind(events, "fail")
    assert eng.live is True
    # one account event per replayed bar
    assert len(_by_kind(events, "account")) == 6
    # BUY_ONCE opens a position on bar 0 and never closes it (no end forced)
    trades = _by_kind(events, "trade")
    assert len(trades) == 0  # still open — no round trip yet
    last = _by_kind(events, "account")[-1].data
    assert len(last["positions"]) == 1
    assert last["balance"] == pytest.approx(10000.0)  # open only locks margin
    assert last["margin_used"] > 0.0


def test_start_no_forced_close_at_end(tmp_path):
    # unlike BacktestEngine.run(), paper start() must NOT force-close the last bar
    payload = make_payload(BUY_ONCE, mode="standard", warmup=0)
    eng = PaperEngine(payload, make_bars(4), storage_path=tmp_path / "storage.json")
    events = list(eng.start())
    trades = _by_kind(events, "trade")
    assert trades == []  # position persists open


def test_start_missing_declare_fails(tmp_path):
    src = """
def on_init(ctx):
    pass
def on_bar(ctx, bar):
    pass
def on_order(ctx, order):
    pass
"""
    payload = make_payload(src, mode="standard", warmup=0)
    eng = PaperEngine(payload, make_bars(4), storage_path=tmp_path / "storage.json")
    events = list(eng.start())
    fails = _by_kind(events, "fail")
    assert len(fails) == 1
    assert "declare" in fails[0].data["message"]
    assert eng.live is False


# ── feed incremental bars ──────────────────────────────────────

def test_feed_appends_and_processes(tmp_path):
    payload = make_payload(BUY_ONCE, mode="standard", warmup=0)
    bars = make_bars(3)
    eng = PaperEngine(payload, bars, storage_path=tmp_path / "storage.json")
    list(eng.start())
    assert len(eng.eng.bars) == 3
    # feed 3 more bars (indices 3..5 of a longer series)
    more = _rows(make_bars(6), 3, 3)
    events = list(eng.feed(more))
    assert len(eng.eng.bars) == 6
    assert len(_by_kind(events, "account")) == 3
    assert not _by_kind(events, "fail")


def test_feed_skips_duplicate_and_stale_times(tmp_path):
    payload = make_payload(BUY_ONCE, mode="standard", warmup=0)
    eng = PaperEngine(payload, make_bars(4), storage_path=tmp_path / "storage.json")
    list(eng.start())
    n0 = len(eng.eng.bars)
    # re-send the last bar (dup) + an older bar (stale) → both skipped
    dup = [[int(eng.eng.bars.time[-1]), 1.1, 1.1, 1.1, 1.1, 100, 20]]
    stale = [[T0 - 3600, 1.1, 1.1, 1.1, 1.1, 100, 20]]
    events = list(eng.feed(dup + stale))
    assert len(eng.eng.bars) == n0
    assert _by_kind(events, "account") == []


def test_feed_processes_only_new_bars_for_orders(tmp_path):
    # a strategy that buys on the 5th bar (index 4) which is fed incrementally
    src = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_bar(ctx, bar):
    i = int((bar.time.timestamp() - %d) / 3600)
    if i == 4 and not ctx.storage.get("done"):
        ctx.storage["done"] = True
        r = ctx.buy("EURUSD", 0.1)
        ctx.log.info(f"FED-BUY:{r.success}")

def on_order(ctx, order):
    pass
""" % T0
    payload = make_payload(src, mode="standard", warmup=0)
    eng = PaperEngine(payload, make_bars(4), storage_path=tmp_path / "storage.json")
    start_events = list(eng.start())
    assert not any("FED-BUY" in m for m in _log_messages(start_events))
    feed_events = list(eng.feed(_rows(make_bars(6), 4, 2)))
    assert any("FED-BUY:True" in m for m in _log_messages(feed_events))


# ── stop ───────────────────────────────────────────────────────

def test_stop_with_close_emits_forced_trade(tmp_path):
    payload = make_payload(BUY_ONCE, mode="standard", warmup=0)
    eng = PaperEngine(payload, make_bars(4), storage_path=tmp_path / "storage.json")
    list(eng.start())
    events = list(eng.stop(close_positions=True))
    trades = _by_kind(events, "trade")
    assert len(trades) == 1
    assert trades[0].data["is_forced"] is True
    assert trades[0].data["comment"] == "end-of-run"
    assert len(_by_kind(events, "account")) == 1
    # position cleared
    assert _by_kind(events, "account")[-1].data["positions"] == []


def test_stop_without_close_keeps_position(tmp_path):
    payload = make_payload(BUY_ONCE, mode="standard", warmup=0)
    eng = PaperEngine(payload, make_bars(4), storage_path=tmp_path / "storage.json")
    list(eng.start())
    events = list(eng.stop(close_positions=False))
    assert _by_kind(events, "trade") == []
    assert len(_by_kind(events, "account")[-1].data["positions"]) == 1


# ── storage persistence / recovery ─────────────────────────────

def test_storage_persisted_and_restored(tmp_path):
    src = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_bar(ctx, bar):
    ctx.storage["counter"] = ctx.storage.get("counter", 0) + 1
    if ctx.storage["counter"] == 1:
        ctx.buy("EURUSD", 0.1)

def on_order(ctx, order):
    pass
"""
    path = tmp_path / "storage.json"
    payload = make_payload(src, mode="standard", warmup=0)
    eng = PaperEngine(payload, make_bars(3), storage_path=path)
    list(eng.start())
    assert path.exists()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["counter"] == 3

    # a fresh engine restores storage → counter continues, no re-buy on bar 0
    eng2 = PaperEngine(payload, make_bars(3), storage_path=path)
    eng2.restore_storage()
    assert eng2.eng.ctx._storage._data["counter"] == 3
    events = list(eng2.start())
    # buy only fires on counter==1, which is already past → no new position from bar0
    # (the restored counter is 3, so the ==1 branch never triggers)
    assert _by_kind(events, "trade") == []


# ── on_tick feed (SDK v1.1.0) ─────────────────────────────────

TICK_LOG_SRC = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_tick(ctx, tick):
    ctx.log.info(f"TICK:{tick.bid}:{tick.ask}:{tick.symbol}")

def on_bar(ctx, bar):
    pass

def on_order(ctx, order):
    pass
"""

TICK_BUY_SRC = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_tick(ctx, tick):
    if ctx.storage.get("done"):
        return
    ctx.storage["done"] = True
    q = ctx.get_quote(ctx.symbol)
    ctx.log.info(f"QUOTE:{q.bid}:{q.ask}")
    r = ctx.buy("EURUSD", 0.1)
    ctx.log.info(f"TBUY:{r.success}:{r.filled_price}")

def on_bar(ctx, bar):
    pass

def on_order(ctx, order):
    pass
"""

TICK_FAIL_SRC = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_tick(ctx, tick):
    raise RuntimeError("tick boom")

def on_bar(ctx, bar):
    pass

def on_order(ctx, order):
    pass
"""

# msc 游标需晚于最后一根 bar 的整秒时间（get_quote tick 分支才生效）；
# 报价用字面量短表示，避免增量运算的长尾浮点污染日志断言
TICKS = [
    [(T0 + 36000) * 1000, 1.1, 1.1002],
    [(T0 + 36000) * 1000 + 1, 1.11, 1.1102],
    [(T0 + 36000) * 1000 + 2, 1.12, 1.1202],
]


def test_feed_ticks_calls_on_tick_in_order(tmp_path):
    payload = make_payload(TICK_LOG_SRC, mode="standard", warmup=0)
    eng = PaperEngine(payload, make_bars(2), storage_path=tmp_path / "s.json")
    list(eng.start())
    events = list(eng.feed_ticks(TICKS))
    assert not _by_kind(events, "fail")
    msgs = _log_messages(events)
    assert [m for m in msgs if m.startswith("TICK:")] == [
        "TICK:1.1:1.1002:EURUSD",
        "TICK:1.11:1.1102:EURUSD",
        "TICK:1.12:1.1202:EURUSD",
    ]
    # 批次粒度：整批 tick 只产生一次账户快照
    assert len(_by_kind(events, "account")) == 1


def test_feed_ticks_buy_fills_at_ask(tmp_path):
    payload = make_payload(TICK_BUY_SRC, mode="standard", warmup=0,
                           slippage_points=0.0)
    eng = PaperEngine(payload, make_bars(2), storage_path=tmp_path / "s.json")
    list(eng.start())
    events = list(eng.feed_ticks([TICKS[0]]))
    msgs = _log_messages(events)
    assert any("QUOTE:1.1:1.1002" in m for m in msgs)  # get_quote 读真实 tick
    assert any("TBUY:True:1.1002" in m for m in msgs)  # buy 按 ask 成交
    last = _by_kind(events, "account")[-1].data
    assert len(last["positions"]) == 1
    assert last["positions"][0]["open_price"] == pytest.approx(1.1002)
    assert not _by_kind(events, "fail")


# ── P1-6 tick fill 确定性测试 ──────────────────────────────

TICK_SELL_SRC = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_tick(ctx, tick):
    if ctx.storage.get("done"):
        return
    ctx.storage["done"] = True
    r = ctx.sell("EURUSD", 0.1)
    ctx.log.info(f"TSELL:{r.success}:{r.filled_price}")

def on_bar(ctx, bar):
    pass

def on_order(ctx, order):
    pass
"""


def test_feed_ticks_sell_fills_at_bid(tmp_path):
    """P1-6: tick phase sell 必须按 tick bid（非 ask）成交，不付 spread。"""
    payload = make_payload(TICK_SELL_SRC, mode="standard", warmup=0,
                           slippage_points=0.0)
    eng = PaperEngine(payload, make_bars(2), storage_path=tmp_path / "s.json")
    list(eng.start())
    events = list(eng.feed_ticks([TICKS[0]]))
    msgs = _log_messages(events)
    # tick: bid=1.1, ask=1.1002 → sell fills at bid = 1.1
    assert any("TSELL:True:1.1" in m for m in msgs)
    pos = _by_kind(events, "account")[-1].data["positions"][0]
    assert pos["direction"] == "sell"
    assert pos["open_price"] == pytest.approx(1.1)
    assert not _by_kind(events, "fail")


def test_feed_ticks_buy_adds_slippage(tmp_path):
    """P1-6: tick phase buy 在 ask 基础上额外加 slippage。"""
    # point 默认 0.00001；slippage=3pts → slip_p = 0.00003
    # tick ask = 1.1002 → fill price = 1.1002 + 0.00003 = 1.10023
    payload = make_payload(TICK_BUY_SRC, mode="standard", warmup=0,
                           slippage_points=3.0)
    eng = PaperEngine(payload, make_bars(2), storage_path=tmp_path / "s.json")
    list(eng.start())
    events = list(eng.feed_ticks([TICKS[0]]))
    msgs = _log_messages(events)
    assert any("QUOTE:1.1:1.1002" in m for m in msgs)
    # buy: ask(1.1002) + slippage(3*0.00001) = 1.10023
    assert any("TBUY:True:1.10023" in m for m in msgs)
    pos = _by_kind(events, "account")[-1].data["positions"][0]
    assert pos["open_price"] == pytest.approx(1.10023)
    assert not _by_kind(events, "fail")


def test_feed_ticks_without_on_tick_has_no_side_effect(tmp_path):
    payload = make_payload(BUY_ONCE, mode="standard", warmup=0)
    eng = PaperEngine(payload, make_bars(2), storage_path=tmp_path / "s.json")
    list(eng.start())
    events = list(eng.feed_ticks(TICKS))
    assert not _log_messages(events)  # on_tick 从未被调用
    assert not _by_kind(events, "fail")


def test_feed_ticks_runtime_error_fails_engine(tmp_path):
    payload = make_payload(TICK_FAIL_SRC, mode="standard", warmup=0)
    eng = PaperEngine(payload, make_bars(2), storage_path=tmp_path / "s.json")
    list(eng.start())
    events = list(eng.feed_ticks([TICKS[0]]))
    fails = _by_kind(events, "fail")
    assert len(fails) == 1
    assert fails[0].data["code"] == "RUNTIME_ERROR"
    assert "tick boom" in fails[0].data["message"]
    assert eng.eng._halted is True
    # 引擎已 halt → 后续 tick 批次空转
    assert list(eng.feed_ticks(TICKS[1:])) == []
