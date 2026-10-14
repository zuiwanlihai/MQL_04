"""Gateway self-check + ops tests (M11).

Reuses the fake MetaTrader5 module pattern from test_trade (no real terminal).
OS probes (powercfg / w32tm / disk) are monkeypatched so assertions are
deterministic regardless of the host machine.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


FAKE = _NS(terminal=None, account=None)


def _make_mt5_module() -> types.ModuleType:
    m = types.ModuleType("MetaTrader5")
    m.__version__ = "5.0.0-fake"

    def initialize(**kw):
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

    for fn in (initialize, shutdown, last_error, terminal_info, account_info,
               symbols_total):
        setattr(m, fn.__name__, fn)
    return m


sys.modules["MetaTrader5"] = _make_mt5_module()

from app import ops  # noqa: E402
from app import selfcheck as sc  # noqa: E402
from app.mt5_client import MT5Client  # noqa: E402


@pytest.fixture
def client():
    # Re-bind the fake MetaTrader5 before constructing the client so this
    # file's FAKE is used regardless of which test module was collected last.
    sys.modules["MetaTrader5"] = _make_mt5_module()
    c = MT5Client()
    yield c
    _run(c.shutdown())


def _run(coro):
    return asyncio.run(coro)


def _ok_terminal():
    FAKE.terminal = _NS(connected=True, trade_allowed=True, build="4000")
    FAKE.account = _NS(login=91350, server="DBGMarkets-Demo")


def _neutralize_os_probes(monkeypatch):
    """Force power/time/disk to pass so MT5-item logic is isolated."""
    async def fake_power():
        return {"ok": True, "sleep_ac": 0, "hibernate_ac": 0, "detail": "AC 睡眠=0s"}

    def fake_w32tm():
        return 0, "Last Successful Time Sync: 2026-10-11 10:00:00"

    class _Usage:
        free = 50 * 1024**3

    monkeypatch.setattr(sc, "probe_power_plan", fake_power)
    monkeypatch.setattr(sc, "_w32tm_sync", fake_w32tm)
    monkeypatch.setattr(sc.shutil, "disk_usage", lambda p: _Usage())


def _by_key(items):
    return {i["key"]: i for i in items}


# ── selfcheck ───────────────────────────────────────────────────

def test_selfcheck_all_pass(client, monkeypatch):
    _ok_terminal()
    _neutralize_os_probes(monkeypatch)
    out = _run(sc.run_selfcheck(client))
    assert out["all_pass"] is True
    by = _by_key(out["items"])
    assert by["terminal_process"]["status"] == "pass"
    assert by["terminal_login"]["status"] == "pass"
    assert by["broker_connected"]["status"] == "pass"
    assert by["algo_trade"]["status"] == "pass"
    assert by["power_plan"]["status"] == "pass"
    assert by["time_sync"]["status"] == "pass"
    assert by["disk_space"]["status"] == "pass"


def test_selfcheck_algo_trade_fail_has_hint(client, monkeypatch):
    FAKE.terminal = _NS(connected=True, trade_allowed=False, build="4000")
    FAKE.account = _NS(login=91350, server="x")
    _neutralize_os_probes(monkeypatch)
    out = _run(sc.run_selfcheck(client))
    by = _by_key(out["items"])
    assert by["algo_trade"]["status"] == "fail"
    assert "算法交易" in by["algo_trade"]["fix_hint"]


def test_selfcheck_power_fail_carries_commands(client, monkeypatch):
    _ok_terminal()

    async def bad_power():
        return {"ok": False, "sleep_ac": 600, "hibernate_ac": 0, "detail": "AC 睡眠=600s"}

    def fake_w32tm():
        return 0, "ok"

    class _U:
        free = 50 * 1024**3

    monkeypatch.setattr(sc, "probe_power_plan", bad_power)
    monkeypatch.setattr(sc, "_w32tm_sync", fake_w32tm)
    monkeypatch.setattr(sc.shutil, "disk_usage", lambda p: _U())

    out = _run(sc.run_selfcheck(client))
    by = _by_key(out["items"])
    assert by["power_plan"]["status"] == "fail"
    assert any("standby-timeout-ac" in c for c in by["power_plan"]["fix_commands"])


def test_selfcheck_terminal_down_cascades(client, monkeypatch):
    FAKE.terminal = None
    FAKE.account = None
    _neutralize_os_probes(monkeypatch)
    out = _run(sc.run_selfcheck(client))
    by = _by_key(out["items"])
    assert by["terminal_process"]["status"] == "fail"
    assert by["terminal_login"]["status"] == "fail"
    assert by["broker_connected"]["status"] == "fail"
    assert out["all_pass"] is False


# ── abnormal-restart state file ─────────────────────────────────

def test_state_file_detects_unclean_previous_run(tmp_path, monkeypatch):
    state = tmp_path / "gateway_state.json"
    monkeypatch.setattr(ops, "STATE_FILE", state)

    # first boot: no prior file → not abnormal
    assert ops.write_state_file() is None
    assert state.exists()

    # simulate a crash: file left behind → next boot sees it
    prev = ops.write_state_file()
    assert prev is not None and "pid" in prev

    ops.clear_state_file()
    assert not state.exists()
    assert ops.write_state_file() is None  # clean again


def test_health_payload_includes_abnormal_flag(client, monkeypatch):
    FAKE.terminal = _NS(connected=True, trade_allowed=True, build="1")
    FAKE.account = _NS(login=1, server="s", name="", currency="USD", leverage=100,
                       balance=0.0, credit=0.0, equity=0.0, margin=0.0, margin_free=0.0,
                       margin_level=0.0, trade_allowed=True, trade_mode=0)
    monkeypatch.setattr(ops, "ABNORMAL_RESTART", {"pid": 123})
    payload = _run(client.health())
    assert payload["abnormal_restart"] is True
    assert payload["abnormal_restart_info"]["pid"] == 123
    monkeypatch.setattr(ops, "ABNORMAL_RESTART", None)
    payload2 = _run(client.health())
    assert payload2["abnormal_restart"] is False


# ── terminal guard give-up cap ──────────────────────────────────

def test_terminal_guard_gives_up_after_cap(client, monkeypatch):
    FAKE.terminal = None  # unreachable
    monkeypatch.setattr(ops.settings, "TERMINAL_GUARD_MAX_RESTARTS", 2)
    monkeypatch.setattr(ops.settings, "TERMINAL_GUARD_WINDOW_SECONDS", 600)

    guard = ops.TerminalGuard(client)
    launched = []
    monkeypatch.setattr(guard, "_launch_terminal_sync", lambda: launched.append(1) or True)

    r1 = _run(guard.probe_and_heal())
    r2 = _run(guard.probe_and_heal())
    assert r1["launched"] is True and r2["launched"] is True
    # 3rd tick: over the cap → give up, no more launches
    r3 = _run(guard.probe_and_heal())
    assert r3["gave_up"] is True and r3["launched"] is False
    assert len(launched) == 2
