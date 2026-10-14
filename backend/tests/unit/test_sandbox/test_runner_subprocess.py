"""Real-subprocess sandbox enforcement via RunnerSupervisor (slow)."""

from __future__ import annotations

import uuid

from app.sandbox.payload import save_payload
from app.services.backtest.supervisor import (
    TERM_COMPLETED,
    TERM_FAILED,
    RunnerSupervisor,
)
from tests.unit.bt_helpers import BUY_ONCE, make_bars, make_payload


async def _run_subprocess(tmp_path, source: str, mode: str = "standard"):
    payload = make_payload(source, mode=mode, warmup=0)
    save_payload(payload, make_bars(12), tmp_path)
    sup = RunnerSupervisor(str(uuid.uuid4()), tmp_path)
    events: list[dict] = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    result = await sup.run(on_event)
    return result, events


def _fail_event(events):
    return next((e for e in events if e.get("event") == "fail"), None)


async def test_happy_path_completed(tmp_path):
    result, events = await _run_subprocess(tmp_path, BUY_ONCE)
    assert result.outcome == TERM_COMPLETED
    assert result.exit_code == 0
    assert any(e.get("event") == "done" for e in events)
    assert any(e.get("event") == "equity" for e in events)


async def test_large_equity_curve_streams_without_pipe_overflow(tmp_path):
    # 回归：1500 点权益曲线单行 JSON >64KB，曾触发 asyncio 子进程流默认
    # readline 上限（LimitOverrunError）导致任务崩溃。
    payload = make_payload(BUY_ONCE, mode="standard", warmup=0)
    save_payload(payload, make_bars(1600), tmp_path)
    sup = RunnerSupervisor(str(uuid.uuid4()), tmp_path)
    events: list[dict] = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    result = await sup.run(on_event)
    assert result.outcome == TERM_COMPLETED
    eq = next(e for e in events if e.get("event") == "equity")
    assert len(eq["curve"]) == 1500
    # 曲线 JSON 字节数必须确实超过 asyncio 默认 64KB 上限（否则回归无效）
    import json

    assert len(json.dumps(eq, ensure_ascii=False).encode("utf-8")) > 64 * 1024


async def test_illegal_import_socket(tmp_path):
    src = """
import socket

def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    pass

def on_order(ctx, order):
    pass
"""
    result, events = await _run_subprocess(tmp_path, src)
    assert result.outcome == TERM_FAILED
    fail = _fail_event(events)
    assert fail is not None
    assert fail["code"] == "ILLEGAL_IMPORT"
    assert fail.get("load_failed") is True


async def test_numpy_load_escape(tmp_path):
    src = """
import numpy as np

def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    np.load("x.npy")

def on_order(ctx, order):
    pass
"""
    result, events = await _run_subprocess(tmp_path, src)
    assert result.outcome == TERM_FAILED
    fail = _fail_event(events)
    assert fail["code"] == "SANDBOX_VIOLATION"


async def test_datetime_now_escape(tmp_path):
    src = """
import datetime

def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    datetime.datetime.now()

def on_order(ctx, order):
    pass
"""
    result, events = await _run_subprocess(tmp_path, src)
    fail = _fail_event(events)
    assert fail["code"] == "SANDBOX_VIOLATION"


async def test_eval_removed_from_builtins(tmp_path):
    src = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    eval("1+1")

def on_order(ctx, order):
    pass
"""
    result, events = await _run_subprocess(tmp_path, src)
    fail = _fail_event(events)
    assert fail["code"] == "RUNTIME_ERROR"
    assert "eval" in fail["message"]


async def test_getattr_escape_caught_at_runtime(tmp_path):
    # 静态校验无法拦截 getattr(np, "load")，运行时守卫层必须拦住
    src = """
import numpy as np

def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    fn = getattr(np, "load")
    fn("x.npy")

def on_order(ctx, order):
    pass
"""
    result, events = await _run_subprocess(tmp_path, src)
    fail = _fail_event(events)
    assert fail["code"] == "SANDBOX_VIOLATION"
