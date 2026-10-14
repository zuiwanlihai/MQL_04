"""PaperManager unit tests: history selection (M5) + tick poll (SDK v1.1.0)."""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import app.services.live.manager as live_manager_mod
import app.services.paper.manager as paper_manager_mod
from app.config import settings
from app.services.gateway_client import GatewayError
from app.services.paper.manager import PaperManager
from app.services.run_base import _chunks, _select_history

TF = 3600  # H1


def _bar(t: int) -> dict:
    return {"time": t, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
            "tick_volume": 1, "spread": 20}


def test_drops_still_forming_live_bar():
    now = 10 * TF
    rows = [_bar(8 * TF), _bar(9 * TF), _bar(10 * TF)]  # last one not closed yet
    out = _select_history(rows, TF, now, need=10)
    assert [int(r["time"]) for r in out] == [8 * TF, 9 * TF]


def test_keeps_weekend_gap_bars_regardless_of_age():
    # bars from last Friday, "now" is Saturday — old but closed → all kept
    now = 100 * TF
    rows = [_bar(t) for t in range(0, 10 * TF, TF)]
    out = _select_history(rows, TF, now, need=10)
    assert len(out) == 10


def test_trims_to_last_need():
    now = 20 * TF
    rows = [_bar(t) for t in range(0, 20 * TF, TF)]
    out = _select_history(rows, TF, now, need=5)
    assert [int(r["time"]) for r in out] == [15 * TF, 16 * TF, 17 * TF, 18 * TF, 19 * TF]


def test_empty_input_returns_empty():
    assert _select_history([], TF, 100, need=10) == []


def test_all_live_bars_returns_empty():
    now = 5
    rows = [_bar(now), _bar(now + 1)]  # every bar still forming
    assert _select_history(rows, TF, now, need=10) == []


# ── tick poll orchestration (SDK v1.1.0) ──────────────────────

def test_chunks_splits_and_covers_all():
    assert _chunks([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert _chunks([], 3) == []


class FakeSup:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.ok = True

    async def send(self, cmd: dict) -> bool:
        self.sent.append(cmd)
        return self.ok


def _install_fake_gateway(monkeypatch, mod) -> SimpleNamespace:
    """Replace the module-level GatewayClient with a scriptable fake."""
    gw = SimpleNamespace(rows=[], error=None, calls=[])

    class _FakeClient:
        def __init__(self, url=None) -> None:
            pass

        @classmethod
        def for_registered_node(cls, url=None):
            return cls(url)

        async def ticks_range(self, symbol, start_s, end_s, limit=100_000):
            gw.calls.append((symbol, start_s, end_s, limit))
            if gw.error is not None:
                raise gw.error
            return list(gw.rows)

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(mod, "GatewayClient", _FakeClient)
    return gw


def _mk_handle(**kw) -> SimpleNamespace:
    d = dict(
        run_id="r1", sup=FakeSup(), symbol="EURUSD", gateway_url="",
        node_type="python", ea_node_id="",
        paused=asyncio.Event(), stopping=False, child_live=True,
        last_tick_msc=1_000_000, has_on_tick=True, tick_task=None, busy=False,
    )
    d.update(kw)
    return SimpleNamespace(**d)


async def _until(cond, timeout=5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("条件未在超时前满足")


async def _run_poll_briefly(mgr, run_id: str, cond) -> None:
    """Run _tick_poll until the sync predicate holds, then cancel it."""
    task = asyncio.create_task(mgr._tick_poll(run_id))
    try:
        await _until(cond)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


GW_ROWS = [
    {"time_msc": 900_000, "bid": 1.0, "ask": 1.0002},    # stale — dropped
    {"time_msc": 1_500_000, "bid": 1.1, "ask": 1.1002},
    {"time_msc": 1_700_000, "bid": 1.2, "ask": 1.2002},
]


async def test_paper_tick_poll_sends_fresh_and_advances_cursor(monkeypatch):
    monkeypatch.setattr(settings, "TICK_POLL_SECONDS", 0.01)
    gw = _install_fake_gateway(monkeypatch, paper_manager_mod)
    gw.rows = GW_ROWS
    mgr = PaperManager()
    h = _mk_handle()
    mgr._handles["r1"] = h
    # 两轮调用后：第一轮已发送（send 在 call2 之前串行完成，无竞态）
    await _run_poll_briefly(mgr, "r1", lambda: len(gw.calls) >= 2)
    assert h.sup.sent == [{"cmd": "ticks", "rows": [
        [1_500_000, 1.1, 1.1002], [1_700_000, 1.2, 1.2002],
    ]}]
    assert h.last_tick_msc == 1_700_000
    # 游标推进：第二轮拉取起点 = last_tick_msc // 1000（limit 截断自然续追）
    assert gw.calls[0][1] == 1_000
    assert gw.calls[1][1] == 1_700


async def test_paper_tick_poll_gated_until_started(monkeypatch):
    monkeypatch.setattr(settings, "TICK_POLL_SECONDS", 0.01)
    gw = _install_fake_gateway(monkeypatch, paper_manager_mod)
    mgr = PaperManager()
    h = _mk_handle(child_live=False)  # warmup 回放未结束
    mgr._handles["r1"] = h
    task = asyncio.create_task(mgr._tick_poll("r1"))
    try:
        await asyncio.sleep(0.08)  # 数个轮询周期
        assert gw.calls == [] and h.sup.sent == []
        h.child_live = True  # started 事件翻转 gate
        gw.rows = [{"time_msc": 1_500_000, "bid": 1.1, "ask": 1.1002}]
        await _until(lambda: len(h.sup.sent) == 1)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_paper_tick_poll_gateway_error_tolerated(monkeypatch):
    monkeypatch.setattr(settings, "TICK_POLL_SECONDS", 0.01)
    gw = _install_fake_gateway(monkeypatch, paper_manager_mod)
    gw.error = GatewayError("网关不可达")
    mgr = PaperManager()
    h = _mk_handle()
    mgr._handles["r1"] = h
    task = asyncio.create_task(mgr._tick_poll("r1"))
    try:
        await _until(lambda: len(gw.calls) >= 2)  # 持续重试而非崩溃
        assert h.sup.sent == [] and h.last_tick_msc == 1_000_000
        gw.error = None  # 网关恢复后自动续传
        gw.rows = [{"time_msc": 1_600_000, "bid": 1.1, "ask": 1.1002}]
        await _until(lambda: len(h.sup.sent) == 1)
        assert h.last_tick_msc == 1_600_000
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_paper_tick_poll_send_failure_exits_cleanly(monkeypatch):
    monkeypatch.setattr(settings, "TICK_POLL_SECONDS", 0.01)
    gw = _install_fake_gateway(monkeypatch, paper_manager_mod)
    gw.rows = [{"time_msc": 1_500_000, "bid": 1.1, "ask": 1.1002}]
    mgr = PaperManager()
    h = _mk_handle()
    h.sup.ok = False  # 子进程已消失
    mgr._handles["r1"] = h
    task = asyncio.create_task(mgr._tick_poll("r1"))
    # send 返回 False → 协程干净返回（stream task 负责收尾）
    await asyncio.wait_for(task, timeout=5.0)


async def test_live_tick_poll_holds_while_order_in_flight(monkeypatch):
    monkeypatch.setattr(settings, "TICK_POLL_SECONDS", 0.01)
    gw = _install_fake_gateway(monkeypatch, live_manager_mod)
    gw.rows = [{"time_msc": 1_500_000, "bid": 1.1, "ask": 1.1002}]
    mgr = live_manager_mod.LiveManager()
    h = _mk_handle(busy=True)  # order RPC in flight — tick 暂缓
    mgr._handles["r1"] = h
    task = asyncio.create_task(mgr._tick_poll("r1"))
    try:
        await asyncio.sleep(0.08)
        assert gw.calls == [] and h.sup.sent == []
        h.busy = False
        await _until(lambda: len(h.sup.sent) == 1)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_build_payload_has_on_tick_default_false():
    mgr = PaperManager()
    run = SimpleNamespace(
        id="r1", strategy_id="s1", source_code="def on_bar(ctx, bar):\n    pass",
        execution_mode="standard", symbol="EURUSD", timeframe="H1",
        initial_deposit=10000.0, leverage=100, spread_mode="current",
        spread_points=None, commission_per_lot=0.0, slippage_points=None,
        warmup_bars=0, parameters={}, config_snapshot={},
    )
    info = {"digits": 5, "point": 0.00001, "trade_contract_size": 100000.0,
            "currency_base": "EUR", "currency_profit": "USD",
            "swap_long": 0.0, "swap_short": 0.0, "trade_mode": 4}
    assert mgr._build_payload(run, info, 20.0).has_on_tick is False
    assert mgr._build_payload(run, info, 20.0, has_on_tick=True).has_on_tick is True
