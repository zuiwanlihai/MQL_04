"""2.2 — paper/live 的父侧兜底：子进程卡死必须能被杀掉。

长驻 runner 传 ``timeout=None``（不能按回测的墙钟上限杀它），于是**唯一的
杀开关就没了**。补上的是 stall 判定：子进程由一条不跑策略代码的线程定期打
心跳，所以「连续 N 秒一行都没收到」是明确的卡死，而不是行情清淡。

这里同时钉住心跳的边界：它必须被父进程剥离，不能漏进引擎事件流。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid

from app.sandbox.runner_io import HEARTBEAT_EVENT, RunnerWriter
from app.services.backtest.supervisor import (
    TERM_COMPLETED,
    TERM_FAILED,
    TERM_STALLED,
    RunnerSupervisor,
)

_SILENT = "tests.unit.test_sandbox.silent_runner"
_HEARTBEAT = "tests.unit.test_sandbox.heartbeat_runner"
_WEDGE = "tests.unit.test_sandbox.wedge_runner"


async def _noop(_ev: dict) -> None:
    return None


async def _drain(sup: RunnerSupervisor, timeout: float = 30.0):
    events: list[dict] = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    result = await asyncio.wait_for(
        sup.run(on_event, timeout=None), timeout=timeout
    )
    return result, events


# ── 父侧 stall 判定 ────────────────────────────────────────────────


async def test_silent_child_is_killed_by_the_stall_detector(tmp_path):
    """连心跳都没有 ⇒ 判定卡死、杀掉进程树，而不是无限期等下去。"""
    sup = RunnerSupervisor(
        str(uuid.uuid4()), tmp_path, module=_SILENT, stall_timeout=1.5
    )
    result, events = await _drain(sup)

    assert result.outcome == TERM_STALLED
    assert events == [], "沉默的子进程本来就不该产出事件"
    assert not await sup.send({"cmd": "status"}), "判定卡死后进程必须真的没了"


async def test_no_stall_timeout_means_no_kill(tmp_path):
    """不传 stall_timeout（回测路径）时行为不变——回测有自己的墙钟上限。"""
    sup = RunnerSupervisor(str(uuid.uuid4()), tmp_path, module=_SILENT)
    assert sup.stall_timeout is None

    # 沉默 2s 后仍不应被杀：既没到墙钟上限，也没启用卡死判定
    run_task = asyncio.create_task(sup.run(_noop, timeout=None))
    await asyncio.sleep(2.0)
    assert not run_task.done(), "未启用 stall 判定时不得杀进程"

    sup.request_cancel()
    result = await asyncio.wait_for(run_task, timeout=15.0)
    assert result.outcome != TERM_STALLED


async def test_heartbeats_are_stripped_from_the_event_stream(tmp_path):
    """心跳是传输层保活，不能漏进 result.events。"""
    sup = RunnerSupervisor(
        str(uuid.uuid4()), tmp_path, module=_HEARTBEAT, stall_timeout=10.0
    )
    result, events = await _drain(sup)

    assert result.outcome == TERM_COMPLETED
    kinds = [e["event"] for e in events]
    assert kinds == ["log", "done"], f"心跳漏进了事件流：{kinds}"


# ── 子进程内兜底：熔断投递不进去时硬退出 ────────────────────────────


async def test_wedge_reaper_ends_an_uninterruptible_process(tmp_path):
    """心跳照常但回调永不返回 ⇒ 由子进程内的 reaper 结束自己，而非父侧。

    这一层覆盖熔断投递不进去的情况（异步异常要回到字节码边界才生效）。它必须
    在 ``os._exit`` **之前**把失败事件写出去，否则运维只会看到一次无声退出。
    """
    sup = RunnerSupervisor(
        str(uuid.uuid4()), tmp_path, module=_WEDGE, stall_timeout=60.0
    )
    result, events = await _drain(sup)

    assert result.outcome == TERM_FAILED
    assert result.exit_code == 2  # EXIT_SAFETY
    fail = next(e for e in events if e.get("event") == "fail")
    assert fail["code"] == "RESOURCE_TIMEOUT"
    assert "强制结束" in fail["message"], fail["message"]


# ── RunnerWriter：心跳与整行原子性 ─────────────────────────────────


class _TearingStdout:
    """模拟「写入不是原子的」stdout：write 中途会让出，暴露并发交错。"""

    def __init__(self) -> None:
        self.chunks: list[str] = []

    def write(self, text: str) -> None:
        mid = len(text) // 2
        self.chunks.append(text[:mid])
        time.sleep(0.0005)  # 让出执行权，缺少互斥时下一行会插进来
        self.chunks.append(text[mid:])

    def flush(self) -> None:
        pass


def test_concurrent_emits_never_tear_a_line(monkeypatch):
    """心跳线程与主线程并发写 stdout：没有锁的话两行会交错成废行。"""
    fake = _TearingStdout()
    monkeypatch.setattr("sys.stdout", fake)

    writer = RunnerWriter(interval=0.0)
    writers, per_writer = 4, 50

    def spam(n: int) -> None:
        for i in range(per_writer):
            writer.emit("log", {"message": f"{n}-{i}"})

    threads = [threading.Thread(target=spam, args=(n,)) for n in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    joined = "".join(fake.chunks)
    lines = [ln for ln in joined.splitlines() if ln]
    assert len(lines) == writers * per_writer, "有行被撕开了"
    for line in lines:
        json.loads(line)  # 任何交错都会在这里炸


def test_heartbeat_emits_only_heartbeat_events(monkeypatch):
    fake = _TearingStdout()
    monkeypatch.setattr("sys.stdout", fake)

    writer = RunnerWriter(interval=0.05)
    writer.start_heartbeat()
    time.sleep(0.3)
    writer.stop_heartbeat()

    lines = [ln for ln in "".join(fake.chunks).splitlines() if ln]
    assert lines, "心跳线程一行都没打出来"
    assert all(json.loads(ln)["event"] == HEARTBEAT_EVENT for ln in lines)
