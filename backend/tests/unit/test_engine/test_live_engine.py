"""LiveEngine RPC bridge: warmup rejection, order_request/result, mirror.

The child never trades locally — every ctx.buy/sell/close becomes an
``order_request`` the fake transport answers with a canned ``order_result``.
These tests pin the protocol contract the parent (LiveManager) relies on.
"""

from __future__ import annotations

import pytest

from app.sandbox.import_hook import uninstall_all
from app.sandbox.live_engine import LiveEngine
from tests.unit.bt_helpers import T0, make_bars, make_payload

BUY_EVERY = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_bar(ctx, bar):
    r = ctx.buy("EURUSD", 0.1)
    ctx.log.info(f"RES:{r.success}:{r.error_code}:{r.filled_price}")

def on_order(ctx, order):
    ctx.log.info(f"ORDER:{order.success}:{order.filled_volume}")
"""


class FakeTransport:
    """Records order_requests, replies with a scripted result per request."""

    def __init__(self, results=None):
        self.requests: list[dict] = []
        self.emitted: list[tuple[str, dict]] = []
        self._results = list(results or [])

    def request_order(self, payload: dict) -> dict:
        self.requests.append(payload)
        if self._results:
            return self._results.pop(0)
        return {
            "success": True, "order_id": "777", "filled_price": 1.23456,
            "filled_volume": 0.1, "commission": 0.0,
        }

    def emit(self, kind: str, data: dict) -> None:
        self.emitted.append((kind, data))


@pytest.fixture(autouse=True)
def _clean_sandbox():
    yield
    uninstall_all()


def _by_kind(events, kind):
    return [e for e in events if e.kind == kind]


def _log_messages(events):
    return [e.data["message"] for e in _by_kind(events, "log") if e.data["kind"] == "log"]


def _has(msgs, needle):
    # str-Enum renders as "ErrorCode.NAME" in f-strings (py3.14) — match the name
    return any(needle in m for m in msgs)


# ── warmup: startup replay must never trade ────────────────────

def test_start_replay_rejects_all_orders():
    tp = FakeTransport()
    payload = make_payload(BUY_EVERY, mode="standard", warmup=0)
    eng = LiveEngine(payload, make_bars(5), tp)
    events = list(eng.start())
    assert not _by_kind(events, "fail")
    # warmup_end pinned to full history → zero order_requests during replay
    assert tp.requests == []
    msgs = _log_messages(events)
    assert all(
        ("RES:False:ErrorCode.WARMUP_PERIOD" in m) for m in msgs if "RES:" in m
    )
    assert eng.live is True


def test_feed_bars_are_tradable():
    tp = FakeTransport()
    payload = make_payload(BUY_EVERY, mode="standard", warmup=0)
    bars = make_bars(3)
    eng = LiveEngine(payload, bars, tp)
    list(eng.start())
    assert tp.requests == []
    # feed 2 new bars → 2 order_requests (one per bar)
    more = [
        [T0 + 3600 * i, 1.1, 1.1, 1.1, 1.1, 100, 20] for i in (3, 4)
    ]
    events = list(eng.feed(more))
    assert len(tp.requests) == 2
    assert tp.requests[0]["op"] == "buy"
    assert tp.requests[0]["symbol"] == "EURUSD"
    assert tp.requests[0]["volume"] == pytest.approx(0.1)
    # success → signal event emitted per fill
    assert len(_by_kind(events, "log")) >= 2
    assert any("ORDER:True" in m for m in _log_messages(events))


# ── order result plumbing ──────────────────────────────────────

def test_order_result_error_propagates_to_strategy():
    tp = FakeTransport(results=[
        {"success": False, "error_code": "RISK_BLOCKED",
         "error_message": "风控拦截：超过单笔手数上限"},
    ])
    payload = make_payload(BUY_EVERY, mode="standard", warmup=0)
    eng = LiveEngine(payload, make_bars(2), tp)
    list(eng.start())
    events = list(eng.feed([[T0 + 7200, 1.1, 1.1, 1.1, 1.1, 100, 20]]))
    msgs = _log_messages(events)
    assert _has(msgs, "RES:False:ErrorCode.RISK_BLOCKED")


def test_order_result_updates_mirror():
    mirror = {
        "account": {"balance": 5000.0, "equity": 5100.0, "margin": 100.0,
                    "margin_free": 5000.0, "currency": "USD", "leverage": 100,
                    "login": 91350, "server": "DBGMarkets-Demo", "name": "",
                    "type": "demo", "credit": 0.0, "margin_level": 5100.0,
                    "trade_allowed": True},
        "positions": [{"ticket": 123, "symbol": "EURUSD", "type": "buy",
                       "volume": 0.1, "price_open": 1.1, "price_current": 1.11,
                       "sl": 0.0, "tp": 0.0, "profit": 10.0, "swap": 0.0,
                       "commission": 0.0, "time": T0, "magic": 42,
                       "identifier": 123, "comment": ""}],
    }
    tp = FakeTransport(results=[
        {"success": True, "order_id": "123", "filled_price": 1.1,
         "filled_volume": 0.1, "commission": 0.0, "account": mirror},
    ])
    payload = make_payload(BUY_EVERY, mode="standard", warmup=0)
    eng = LiveEngine(payload, make_bars(2), tp)
    list(eng.start())
    list(eng.feed([[T0 + 7200, 1.1, 1.1, 1.1, 1.1, 100, 20]]))
    acc = eng.ctx.get_account()
    assert acc.balance == pytest.approx(5000.0)
    assert acc.equity == pytest.approx(5100.0)
    positions = eng.ctx.get_positions()
    assert len(positions) == 1
    assert positions[0].id == "123"
    assert positions[0].direction == "buy"
    assert positions[0].unrealized_pnl == pytest.approx(10.0)
    one = eng.ctx.get_position("123")
    assert one is not None and eng.ctx.get_position("999") is None


def test_account_event_reflects_mirror():
    tp = FakeTransport()
    payload = make_payload(BUY_EVERY, mode="standard", warmup=0)
    eng = LiveEngine(payload, make_bars(2), tp)
    eng.apply_mirror({
        "account": {"balance": 1000.0, "equity": 1050.0, "margin": 20.0,
                    "margin_free": 1030.0, "margin_level": 5250.0},
        "positions": [{"ticket": 1, "symbol": "EURUSD", "type": "sell",
                       "volume": 0.2, "price_open": 1.1, "price_current": 1.09,
                       "sl": 0.0, "tp": 0.0, "profit": 2.0, "swap": 0.0,
                       "commission": 0.0, "time": T0, "magic": 42,
                       "identifier": 1, "comment": ""}],
    })
    ev = eng.account_event().data
    assert ev["balance"] == pytest.approx(1000.0)
    assert ev["equity"] == pytest.approx(1050.0)
    assert ev["unrealized_pnl"] == pytest.approx(50.0)
    assert len(ev["positions"]) == 1


def test_close_request_carries_position_id():
    src = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_bar(ctx, bar):
    if ctx.storage.get("opened"):
        r = ctx.close("123")
        ctx.log.info(f"CLOSE:{r.success}")
        return
    ctx.storage["opened"] = True

def on_order(ctx, order):
    pass
"""
    tp = FakeTransport()
    payload = make_payload(src, mode="standard", warmup=0)
    eng = LiveEngine(payload, make_bars(2), tp)
    list(eng.start())
    list(eng.feed([[T0 + 7200, 1.1, 1.1, 1.1, 1.1, 100, 20]]))
    assert len(tp.requests) == 1
    assert tp.requests[0]["op"] == "close"
    assert tp.requests[0]["position_id"] == "123"


def test_unknown_result_from_timeout_is_failure():
    tp = FakeTransport(results=[
        {"success": False, "error_code": "UNKNOWN_ERROR",
         "error_message": "下单结果未知（父进程/网关无响应），已转对账收敛"},
    ])
    payload = make_payload(BUY_EVERY, mode="standard", warmup=0)
    eng = LiveEngine(payload, make_bars(2), tp)
    list(eng.start())
    events = list(eng.feed([[T0 + 7200, 1.1, 1.1, 1.1, 1.1, 100, 20]]))
    msgs = _log_messages(events)
    assert _has(msgs, "RES:False:ErrorCode.UNKNOWN_ERROR")


def test_stop_calls_on_stop_and_persists(tmp_path):
    src = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_bar(ctx, bar):
    pass

def on_order(ctx, order):
    pass

def on_stop(ctx, reason):
    ctx.log.info(f"STOPPED:{reason}")
"""
    tp = FakeTransport()
    path = tmp_path / "storage.json"
    payload = make_payload(src, mode="standard", warmup=0)
    eng = LiveEngine(payload, make_bars(3), tp, storage_path=path)
    list(eng.start())
    events = list(eng.stop(close_positions=True))
    # child never force-closes itself — real closes are the parent's job
    assert not _by_kind(events, "trade")
    assert len(_by_kind(events, "account")) == 1
    assert path.exists()


# ── on_tick feed (SDK v1.1.0) ─────────────────────────────────

TICK_BUY = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_tick(ctx, tick):
    if ctx.storage.get("done"):
        return
    ctx.storage["done"] = True
    r = ctx.buy("EURUSD", 0.1)
    ctx.log.info(f"TBUY:{r.success}:{r.filled_price}")

def on_bar(ctx, bar):
    pass

def on_order(ctx, order):
    pass
"""


def test_start_unpins_warmup_for_on_tick():
    """Regression: warmup_end pinned to len(bars) would reject every tick order
    forever (ticks never advance the bar index).  After start() it is unpinned
    to the last replayed bar so on_tick orders go out immediately."""
    tp = FakeTransport()
    payload = make_payload(TICK_BUY, mode="standard", warmup=0)
    eng = LiveEngine(payload, make_bars(3), tp)
    list(eng.start())
    assert eng.eng.warmup_end == 2  # len(bars) - 1


def test_feed_ticks_order_goes_through_rpc():
    tp = FakeTransport()
    payload = make_payload(TICK_BUY, mode="standard", warmup=0)
    eng = LiveEngine(payload, make_bars(2), tp)
    list(eng.start())
    msc = (T0 + 36000) * 1000
    events = list(eng.feed_ticks([[msc, 1.1, 1.1002]]))
    assert len(tp.requests) == 1
    assert tp.requests[0]["op"] == "buy"
    assert tp.requests[0]["symbol"] == "EURUSD"
    assert tp.requests[0]["volume"] == pytest.approx(0.1)
    # 券商成交价经 RPC 回流到策略（FakeTransport 默认成交 1.23456）
    assert any("TBUY:True:1.23456" in m for m in _log_messages(events))
    assert len(_by_kind(events, "account")) == 1  # 批次末账户快照


def test_feed_ticks_without_on_tick_is_noop():
    tp = FakeTransport()
    payload = make_payload(BUY_EVERY, mode="standard", warmup=0)
    eng = LiveEngine(payload, make_bars(2), tp)
    list(eng.start())
    events = list(eng.feed_ticks([[(T0 + 36000) * 1000, 1.1, 1.1002]]))
    assert tp.requests == []
    assert not _log_messages(events)  # on_tick 从未被调用
