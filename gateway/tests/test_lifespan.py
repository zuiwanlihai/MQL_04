"""Lifespan tests — the batch-1 exit criterion.

"C网关连续两次正常启停无异常重启告警" / two consecutive clean start-stops must
not raise the abnormal-restart alert.

The alert is what keeps an operator honest: it says "the gateway died while
positions were open, go check them by hand".  A state file left behind by an
*ordinary* restart fires it every single boot, which trains the user to dismiss
the one alert that matters.  These tests drive the real ``lifespan`` twice and
assert the flag stays down and the file is cleaned up.

Before the fix this failed at the first cycle: ``MT5Client.shutdown()`` latched
``_shutdown`` before hopping the queue, so ``run()`` rejected the release job,
``shutdown()`` raised, and ``clear_state_file()`` never ran.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


FAKE = _NS(terminal=None, account=None, launches=[])


def _make_mt5_module() -> types.ModuleType:
    m = types.ModuleType("MetaTrader5")
    m.__version__ = "5.0.0-fake"

    def initialize(**kw):
        FAKE.terminal = _NS(connected=True, trade_allowed=True, build="4000")
        FAKE.account = _NS(
            login=91350, server="DBGMarkets-Demo", name="", currency="USD",
            leverage=100, balance=10000.0, credit=0.0, equity=10000.0,
            margin=0.0, margin_free=10000.0, margin_level=0.0,
            trade_allowed=True, trade_mode=0,
        )
        return True

    def shutdown():
        return None

    def last_error():
        return (1, "fake")

    def terminal_info():
        return FAKE.terminal

    def account_info():
        return FAKE.account

    def symbols_total():
        return 10

    def login(login, password, server, **kw):  # noqa: A002 — mt5 param name
        # Reached when the terminal is NOT already on the configured account
        # (``MT5Client.login`` short-circuits on a match).  Must exist: the
        # lifespan auto-login branch calls it whenever .env carries credentials.
        return FAKE.account is not None

    for fn in (initialize, shutdown, last_error, terminal_info, account_info,
               symbols_total, login):
        setattr(m, fn.__name__, fn)
    return m


sys.modules["MetaTrader5"] = _make_mt5_module()

from app import main as main_mod  # noqa: E402
from app import mt5_client as mc  # noqa: E402
from app import ops  # noqa: E402

# Everything that did `from app.mt5_client import client` and therefore holds
# its own reference to the singleton.
_CLIENT_REFS = (
    "app.main",
    "app.routes.account",
    "app.routes.health",
    "app.routes.market",
    "app.routes.selfcheck",
    "app.routes.terminal",
    "app.routes.trade",
)


def _new_process_client(monkeypatch) -> mc.MT5Client:
    """Simulate a fresh process: a brand-new MT5Client in every reference.

    ``MT5Client`` is deliberately single-use — ``shutdown()`` posts the
    sentinel that ends the worker thread for good, so the object cannot serve
    a second cycle (queueing onto it simply never resolves).  A real restart
    is a new process, so give each cycle a new client rather than trying to
    revive the old one.
    """
    import importlib

    # Re-bind this file's fake before constructing the client: pytest imports
    # every test module first, so whichever file ran ``sys.modules[...] = ...``
    # last owns the name.  Without this the client attaches to another file's
    # fake (different `account`, no `login`) and the lifespan misbehaves.
    sys.modules["MetaTrader5"] = _make_mt5_module()

    fresh = mc.MT5Client()
    for name in _CLIENT_REFS:
        monkeypatch.setattr(importlib.import_module(name), "client", fresh)
    return fresh


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    """A state file in tmp_path, and a terminal guard that never spawns one."""
    state = tmp_path / "gateway_state.json"
    monkeypatch.setattr(ops, "STATE_FILE", state)

    # The guard would `subprocess.Popen(settings.MT5_PATH)` on an unreachable
    # terminal — record instead, so a test can never launch a real MT5.
    monkeypatch.setattr(
        ops.TerminalGuard, "_launch_terminal_sync",
        lambda self: FAKE.launches.append(1) or False,
    )
    monkeypatch.setattr(ops, "ABNORMAL_RESTART", None)
    yield state
    assert FAKE.launches == [], "terminal guard tried to launch a real terminal"


def _cycle(monkeypatch) -> dict:
    """One full start→stop of the real app lifespan; returns the /health body."""
    _new_process_client(monkeypatch)
    app = main_mod.create_app()
    with TestClient(app) as c:
        resp = c.get("/health")
        assert resp.status_code == 200
        return resp.json()


def test_two_clean_restarts_never_report_abnormal(gateway, monkeypatch):
    """The exit criterion, end to end, through the real lifespan."""
    for cycle_no in (1, 2):
        body = _cycle(monkeypatch)
        assert body["abnormal_restart"] is False, (
            f"cycle {cycle_no} raised a spurious「请先核对持仓」alert"
        )
    assert not gateway.exists(), "clean exit must remove the state file"


def test_crash_then_boot_still_reports_abnormal(gateway, monkeypatch):
    """The alert must keep working for the case it exists for.

    A run that dies without reaching the clean-exit path leaves the file
    behind — the next boot has to say so.
    """
    import json

    gateway.write_text(
        json.dumps({"pid": 999999, "started_at": 0.0, "started_at_iso": ""}),
        encoding="utf-8",
    )
    body = _cycle(monkeypatch)
    assert body["abnormal_restart"] is True, "genuine crash must still be flagged"


def test_blank_api_key_refuses_to_start(gateway, monkeypatch):
    """2.5：密钥为空时应用拒绝启动——宁可不启动，也不开一个无鉴权的交易端口。

    挡在前面的必须是密钥自检，不是别的启动错误：这里已经装好了假终端与状态
    文件，lifespan 上再无别的可失败项。
    """
    _new_process_client(monkeypatch)
    monkeypatch.setattr(main_mod.settings, "GATEWAY_API_KEY", "")

    app = main_mod.create_app()
    with pytest.raises(RuntimeError, match="GATEWAY_API_KEY"), TestClient(app):
        pass  # 进入 lifespan 就该被拦住

    # 空密钥下不得留下状态文件：那会让下一次启动误报「异常重启」。
    assert not gateway.exists(), "被拒绝的启动不该污染状态文件"
