"""Real-subprocess paper_runner control protocol via RunnerSupervisor (slow)."""

from __future__ import annotations

import asyncio
import uuid

from app.sandbox.payload import save_payload
from app.services.backtest.supervisor import (
    TERM_COMPLETED,
    RunnerSupervisor,
)
from tests.unit.bt_helpers import BUY_ONCE, T0, make_bars, make_payload


async def _wait_events(events: list[dict], kind: str, count: int, timeout=60.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        n = sum(1 for e in events if e.get("event") == kind)
        if n >= count:
            return n
        await asyncio.sleep(0.2)
    raise AssertionError(f"等待 {kind}×{count} 超时（当前 {len(events)} 事件）")


async def test_paper_runner_bars_status_stop(tmp_path):
    bars = make_bars(6)
    payload = make_payload(BUY_ONCE, mode="standard", warmup=0)
    save_payload(payload, bars, tmp_path)
    sup = RunnerSupervisor(str(uuid.uuid4()), tmp_path, module="app.sandbox.paper_runner")
    events: list[dict] = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    run_task = asyncio.create_task(sup.run(on_event, timeout=None))

    # startup replay: one account event per history bar
    await _wait_events(events, "account", 6)
    assert not any(e.get("event") == "fail" for e in events)

    # feed two newly closed bars (continue the synthetic series)
    more = make_bars(8)
    rows = [
        [int(more.time[i]), float(more.open[i]), float(more.high[i]),
         float(more.low[i]), float(more.close[i]), int(more.tick_volume[i]),
         int(more.spread[i])]
        for i in (6, 7)
    ]
    assert await sup.send({"cmd": "bars", "rows": rows})
    await _wait_events(events, "account", 8)

    # status snapshot on demand
    assert await sup.send({"cmd": "status"})
    await _wait_events(events, "account", 9)

    # stop with close → forced trade + final account + exit 0
    assert await sup.send({"cmd": "stop", "close": True})
    result = await asyncio.wait_for(run_task, timeout=30.0)
    assert result.outcome == TERM_COMPLETED
    assert result.exit_code == 0
    trades = [e for e in events if e.get("event") == "trade"]
    assert len(trades) == 1
    assert trades[0]["is_forced"] is True
    # storage.json persisted (recovery contract)
    assert (tmp_path / "storage.json").exists()


async def test_paper_runner_illegal_import_exits_3(tmp_path):
    src = """
import socket

def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    pass

def on_order(ctx, order):
    pass
"""
    payload = make_payload(src, mode="standard", warmup=0)
    save_payload(payload, make_bars(4), tmp_path)
    sup = RunnerSupervisor(str(uuid.uuid4()), tmp_path, module="app.sandbox.paper_runner")
    events: list[dict] = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    result = await asyncio.wait_for(sup.run(on_event, timeout=None), timeout=60.0)
    assert result.exit_code == 3  # EXIT_LOAD_FAILED
    fail = next(e for e in events if e.get("event") == "fail")
    assert fail["code"] == "ILLEGAL_IMPORT"
    assert fail.get("load_failed") is True


TICK_ONCE = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_tick(ctx, tick):
    if ctx.storage.get("t"):
        return
    ctx.storage["t"] = True
    ctx.log.info(f"TICK:{tick.bid}:{tick.ask}")
    r = ctx.buy("EURUSD", 0.1)
    ctx.log.info(f"TBUY:{r.success}:{r.filled_price}")

def on_bar(ctx, bar):
    pass

def on_order(ctx, order):
    pass
"""


async def test_paper_runner_started_gate_and_ticks(tmp_path):
    bars = make_bars(4)
    payload = make_payload(TICK_ONCE, mode="standard", warmup=0,
                           slippage_points=0.0)
    save_payload(payload, bars, tmp_path)
    sup = RunnerSupervisor(str(uuid.uuid4()), tmp_path,
                           module="app.sandbox.paper_runner")
    events: list[dict] = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    run_task = asyncio.create_task(sup.run(on_event, timeout=None))

    # startup replay drains → runner emits "started" (parent-side tick gate)
    await _wait_events(events, "account", 4)
    await _wait_events(events, "started", 1)
    assert not any(e.get("event") == "fail" for e in events)

    # deliver one real tick → on_tick fires and the buy fills at the ask
    msc = (T0 + 36000) * 1000 + 500
    assert await sup.send({"cmd": "ticks", "rows": [[msc, 1.1, 1.1002]]})
    await _wait_events(events, "log", 2)  # TICK: + TBUY:
    logs = [e.get("message") for e in events if e.get("event") == "log"]
    assert any("TICK:1.1:1.1002" in m for m in logs)
    assert any("TBUY:True:1.1002" in m for m in logs)

    # stop with close → forced round-trip trade + exit 0
    assert await sup.send({"cmd": "stop", "close": True})
    result = await asyncio.wait_for(run_task, timeout=30.0)
    assert result.outcome == TERM_COMPLETED
    assert result.exit_code == 0
    trades = [e for e in events if e.get("event") == "trade"]
    assert len(trades) == 1
    assert trades[0]["is_forced"] is True
