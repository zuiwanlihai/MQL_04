"""Real-subprocess live_runner order RPC via RunnerSupervisor (slow).

Proves the child↔parent contract end to end: a strategy's ctx.buy surfaces as
an ``order_request`` event, the parent answers with an ``order_result`` written
to stdin, and the child unblocks, re-enters on_order, and keeps streaming.
"""

from __future__ import annotations

import asyncio
import uuid

from app.sandbox.payload import save_payload
from app.services.backtest.supervisor import (
    TERM_COMPLETED,
    RunnerSupervisor,
)
from tests.unit.bt_helpers import T0, make_bars, make_payload

# buys only on the fed bar (T0+4h) — the replay region is warmup-rejected and
# must not consume the "done" flag, otherwise the RPC path would never fire
BUY_ONCE = f"""
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={{}})

def on_bar(ctx, bar):
    if int(bar.time.timestamp()) < {T0 + 4 * 3600} or ctx.storage.get("done"):
        return
    ctx.storage["done"] = True
    r = ctx.buy("EURUSD", 0.1)
    ctx.log.info(f"BUY:{{r.success}}:{{r.filled_price}}")

def on_order(ctx, order):
    ctx.log.info(f"ORDER:{{order.success}}:{{order.filled_volume}}")
"""


async def _wait(events: list[dict], pred, timeout=60.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        hit = next((e for e in events if pred(e)), None)
        if hit is not None:
            return hit
        await asyncio.sleep(0.2)
    raise AssertionError("等待事件超时")


async def test_live_runner_order_rpc_roundtrip(tmp_path):
    bars = make_bars(4)
    payload = make_payload(BUY_ONCE, mode="standard", warmup=0)
    save_payload(payload, bars, tmp_path)
    sup = RunnerSupervisor(str(uuid.uuid4()), tmp_path, module="app.sandbox.live_runner")
    events: list[dict] = []
    answered: set[str] = set()

    async def on_event(ev: dict) -> None:
        events.append(ev)
        if ev.get("event") == "order_request" and ev["req_id"] not in answered:
            answered.add(ev["req_id"])
            assert ev["op"] == "buy"
            assert ev["symbol"] == "EURUSD"
            await sup.send({
                "type": "order_result", "req_id": ev["req_id"], "success": True,
                "order_id": "555", "filled_price": 1.10005, "filled_volume": 0.1,
                "commission": 0.0, "error_code": None, "error_message": None,
                "account": {"account": {"balance": 1000.0, "equity": 1000.0,
                                        "margin": 20.0, "margin_free": 980.0},
                            "positions": []},
            })

    run_task = asyncio.create_task(sup.run(on_event, timeout=None))

    # startup replay (warmup pinned to history) → no order_request, just accounts
    await _wait(events, lambda e: e.get("event") == "account")
    assert not any(e.get("event") == "order_request" for e in events)

    # feed a tradable bar → BUY_ONCE fires → order_request arrives
    more = make_bars(6)
    rows = [
        [int(more.time[i]), float(more.open[i]), float(more.high[i]),
         float(more.low[i]), float(more.close[i]), int(more.tick_volume[i]),
         int(more.spread[i])]
        for i in (4, 5)
    ]
    assert await sup.send({"cmd": "bars", "rows": rows})
    req = await _wait(events, lambda e: e.get("event") == "order_request")
    assert req["op"] == "buy"

    # the answered request unblocks → on_order + BUY:True logged
    await _wait(events, lambda e: e.get("event") == "log"
                and "BUY:True" in str(e.get("message", "")))
    await _wait(events, lambda e: e.get("event") == "log"
                and "ORDER:True" in str(e.get("message", "")))

    # account cmd → mirror pushed back
    assert await sup.send({"cmd": "account", "account": {
        "account": {"balance": 1050.0, "equity": 1060.0, "margin": 20.0,
                    "margin_free": 1040.0},
        "positions": [{"ticket": 555, "symbol": "EURUSD", "type": "buy",
                       "volume": 0.1, "price_open": 1.10005,
                       "price_current": 1.10010, "sl": 0.0, "tp": 0.0,
                       "profit": 0.5, "swap": 0.0, "commission": 0.0,
                       "time": 1760000400, "magic": 42, "identifier": 555,
                       "comment": ""}]}})
    acct = await _wait(events, lambda e: e.get("event") == "account"
                       and e.get("balance") == 1050.0)
    assert len(acct["positions"]) == 1

    # stop → exit 0 (child never force-closes; real closes are the parent's job)
    assert await sup.send({"cmd": "stop", "close": True})
    result = await asyncio.wait_for(run_task, timeout=30.0)
    assert result.outcome == TERM_COMPLETED
    assert result.exit_code == 0
    assert not any(e.get("event") == "trade" for e in events)
    assert (tmp_path / "storage.json").exists()


async def test_live_runner_illegal_import_exits_3(tmp_path):
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
    sup = RunnerSupervisor(str(uuid.uuid4()), tmp_path, module="app.sandbox.live_runner")
    events: list[dict] = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    result = await asyncio.wait_for(sup.run(on_event, timeout=None), timeout=60.0)
    assert result.exit_code == 3
    fail = next(e for e in events if e.get("event") == "fail")
    assert fail["code"] == "ILLEGAL_IMPORT"
    assert fail.get("load_failed") is True
