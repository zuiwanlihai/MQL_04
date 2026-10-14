"""Login override tests — /account/login 切换账户后重连必须复用切换凭据.

Covers: _login_sync memorises credentials, _ensure_initialized_sync prefers
the override on reconnect (terminal restart self-heal), _logout_sync clears it.
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


FAKE = _NS(
    terminal=_NS(name="fake"), account=None,
    init_kwargs=None, login_calls=[],
)


def _make_mt5_module() -> types.ModuleType:
    m = types.ModuleType("MetaTrader5")
    m.__version__ = "5.0.0-fake"

    def initialize(**kw):
        FAKE.init_kwargs = kw
        return True

    def shutdown():
        return None

    def last_error():
        return (1, "fake")

    def terminal_info():
        return FAKE.terminal

    def account_info():
        return FAKE.account

    def login(login, password, server):  # noqa: A002 — mt5 param name
        FAKE.login_calls.append((login, password, server))
        return FAKE.login_ok

    def symbols_total():
        return 10

    def logout():
        return None

    for fn in (initialize, shutdown, last_error, terminal_info,
               account_info, login, symbols_total, logout):
        setattr(m, fn.__name__, fn)
    return m


sys.modules["MetaTrader5"] = _make_mt5_module()

from app import mt5_client as mc  # noqa: E402

CRED = (91350, "pw-demo", "DBGMarkets-Demo")


@pytest.fixture
def client():
    sys.modules["MetaTrader5"] = _make_mt5_module()
    c = mc.MT5Client()
    yield c
    asyncio.run(c.shutdown())


def test_login_sync_memorises_override(client, monkeypatch):
    monkeypatch.setattr(FAKE, "login_ok", True, raising=False)
    monkeypatch.setattr(FAKE, "account", _NS(trade_mode=0, login=91350))
    out = client._login_only_sync(*CRED)
    assert out["login"] == 91350
    assert client._login_override == CRED


def test_reconnect_reuses_override_not_env(client, monkeypatch):
    monkeypatch.setattr(FAKE, "login_ok", True, raising=False)
    monkeypatch.setattr(FAKE, "account", _NS(trade_mode=0, login=2015611))
    client._login_only_sync(2015611, "pw-live", "DBGMarkets-Live")

    # 模拟终端重启：terminal_info 掉线 + symbols_total None → 强制重连
    monkeypatch.setattr(FAKE, "terminal", None, raising=False)
    monkeypatch.setattr(mc, "_probe_none_once", True, raising=False)
    # symbols_total 返回 None 模拟陈旧 IPC 缓存
    orig = sys.modules["MetaTrader5"].symbols_total
    sys.modules["MetaTrader5"].symbols_total = lambda: None
    try:
        client._ensure_initialized_sync()
    finally:
        sys.modules["MetaTrader5"].symbols_total = orig

    # 重连凭据必须是 override（live 账户），而非 .env 默认
    assert FAKE.init_kwargs["login"] == 2015611
    assert FAKE.init_kwargs["server"] == "DBGMarkets-Live"


def test_reconnect_falls_back_to_env_without_override(client, monkeypatch):
    monkeypatch.setattr(mc.settings, "MT5_LOGIN", 91350)
    monkeypatch.setattr(mc.settings, "MT5_PASSWORD", "pw-demo")
    monkeypatch.setattr(mc.settings, "MT5_SERVER", "DBGMarkets-Demo")
    monkeypatch.setattr(FAKE, "terminal", None, raising=False)
    orig = sys.modules["MetaTrader5"].symbols_total
    sys.modules["MetaTrader5"].symbols_total = lambda: None
    try:
        client._ensure_initialized_sync()
    finally:
        sys.modules["MetaTrader5"].symbols_total = orig

    assert FAKE.init_kwargs["login"] == 91350
    assert FAKE.init_kwargs["server"] == "DBGMarkets-Demo"


def test_logout_clears_override(client, monkeypatch):
    monkeypatch.setattr(FAKE, "login_ok", True, raising=False)
    monkeypatch.setattr(FAKE, "account", _NS(trade_mode=0, login=91350))
    client._login_only_sync(*CRED)
    assert client._login_override == CRED
    client._logout_sync()
    assert client._login_override is None
