"""POST /terminal/algo-trading — Ctrl+E 注入切换终端「算法交易」按钮。

win32 注入（find_terminal_window / send_ctrl_e）monkeypatch 掉，路由逻辑
（达标短路 / 注入 / 复读验证 / 失败 502）用 fake client 全覆盖，无真实窗口。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeMT5:
    """terminal_info() 返回可变 FAKE.terminal。"""

    def terminal_info(self):
        return FAKE.terminal


class FakeClient:
    """client.run(fn) 直接在事件循环内执行（网关真实实现走 worker 线程）。"""

    def __init__(self) -> None:
        self._mt5 = FakeMT5()

    async def run(self, fn, *a, **k):
        return fn()


FAKE = _NS(terminal=None)

from fastapi import HTTPException  # noqa: E402

from app import win32_keys  # noqa: E402
from app.routes import terminal as terminal_mod  # noqa: E402
from app.schemas import TerminalAlgoRequest  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(terminal_mod, "client", c)
    # 验证轮询即时化（12 轮 × 0.25s → 0s，但保留轮次语义）
    monkeypatch.setattr(terminal_mod, "_VERIFY_INTERVAL", 0.0)
    return c


@pytest.fixture
def inject_log(monkeypatch):
    """记录 send_ctrl_e 调用；默认注入后由测试自己翻转 FAKE.terminal。"""
    log: list[int] = []

    def fake_find():
        return 12345

    def fake_send(hwnd: int) -> None:
        log.append(hwnd)

    monkeypatch.setattr(win32_keys, "find_terminal_window", lambda: fake_find())
    monkeypatch.setattr(win32_keys, "send_ctrl_e", fake_send)
    return log


def _run(coro):
    return asyncio.run(coro)


# ── 已达标短路 ───────────────────────────────────────────────────

def test_already_at_target_short_circuits(client, inject_log):
    FAKE.terminal = _NS(trade_allowed=True)
    out = _run(terminal_mod.set_algo_trading(TerminalAlgoRequest(enabled=True)))
    assert out.changed is False
    assert out.trade_allowed is True
    assert inject_log == []

    FAKE.terminal = _NS(trade_allowed=False)
    out = _run(terminal_mod.set_algo_trading(TerminalAlgoRequest(enabled=False)))
    assert out.changed is False
    assert out.trade_allowed is False
    assert inject_log == []


# ── 注入成功（状态翻转） ─────────────────────────────────────────

def test_ctrl_e_flips_state(client, inject_log, monkeypatch):
    FAKE.terminal = _NS(trade_allowed=False)

    def send_then_flip(hwnd: int) -> None:
        inject_log.append(hwnd)
        FAKE.terminal = _NS(trade_allowed=True)

    monkeypatch.setattr(win32_keys, "send_ctrl_e", send_then_flip)
    out = _run(terminal_mod.set_algo_trading(TerminalAlgoRequest(enabled=True)))
    assert out.changed is True
    assert out.trade_allowed is True
    assert inject_log == [12345]


# ── 注入后状态未翻转 → 502 ───────────────────────────────────────

def test_state_not_changed_after_inject(client, inject_log):
    FAKE.terminal = _NS(trade_allowed=False)
    with pytest.raises(HTTPException) as ei:
        _run(terminal_mod.set_algo_trading(TerminalAlgoRequest(enabled=True)))
    assert ei.value.status_code == 502
    assert "终端状态未变化" in ei.value.detail


# ── 找不到终端窗口 → 502 ─────────────────────────────────────────

def test_no_terminal_window(client, inject_log, monkeypatch):
    FAKE.terminal = _NS(trade_allowed=False)
    monkeypatch.setattr(win32_keys, "find_terminal_window", lambda: None)
    with pytest.raises(HTTPException) as ei:
        _run(terminal_mod.set_algo_trading(TerminalAlgoRequest(enabled=True)))
    assert ei.value.status_code == 502
    assert "未找到 MT5 终端窗口" in ei.value.detail


# ── 注入本身抛异常 → 502 ─────────────────────────────────────────

def test_send_ctrl_e_raises(client, inject_log, monkeypatch):
    FAKE.terminal = _NS(trade_allowed=False)

    def boom(hwnd: int) -> None:
        raise RuntimeError("SendInput failed")

    monkeypatch.setattr(win32_keys, "send_ctrl_e", boom)
    with pytest.raises(HTTPException) as ei:
        _run(terminal_mod.set_algo_trading(TerminalAlgoRequest(enabled=True)))
    assert ei.value.status_code == 502
    assert "SendInput failed" in ei.value.detail


# ── 终端不可达 → 409 ─────────────────────────────────────────────

def test_terminal_unreachable(client, inject_log):
    FAKE.terminal = None
    with pytest.raises(HTTPException) as ei:
        _run(terminal_mod.set_algo_trading(TerminalAlgoRequest(enabled=True)))
    assert ei.value.status_code == 409
    assert "MT5 终端不可达" in ei.value.detail
