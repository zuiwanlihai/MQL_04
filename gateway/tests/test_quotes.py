"""Gateway quotes tests — fake MetaTrader5, no terminal.

Covers the skew 抗污染修复：批次内最多 3 个品种 tick 取中位数估 tz_skew
（单品种喂价时间戳异常不再污染全局）、bar_time 的 min(·, now) 未来钳制、
以及 10 分钟 TTL 内不重估。
"""

from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path

import pytest

# -- fake MetaTrader5 -----------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# symbol → tick.time（服务器时区伪 epoch，秒）
FAKE = _NS(tick_times={})

SKEW = 10_800  # GMT+3 服务器时区偏移
POLLUTION = 1_080  # 喂价时间戳异常：额外超前 18min（实测 XAUUSD）


def _make_mt5_module() -> types.ModuleType:
    m = types.ModuleType("MetaTrader5")
    m.__version__ = "5.0.0-fake"

    def symbol_info_tick(symbol):
        t = FAKE.tick_times.get(symbol)
        if t is None:
            return None
        return _NS(time=int(t), bid=1.1, ask=1.10002, last=0.0, volume=1)

    def copy_rates_range(symbol, timeframe, date_from, date_to):
        return None  # D1 日内高低缓存取空 → prev_close/high_day/low_day = 0

    def initialize(**kw):
        return True

    def shutdown():
        return None

    def last_error():
        return (1, "fake")

    def terminal_info():
        return _NS(connected=True, trade_allowed=True)

    def symbol_select(name, enable):
        return name != "NOSUCH"

    def symbols_total():
        return 10

    def symbol_info(name):
        if name == "NOSUCH":
            return None
        return _NS(name=name, digits=5, point=0.00001, spread=20)

    for fn in (initialize, shutdown, last_error, terminal_info,
               symbol_select, symbol_info, symbols_total, symbol_info_tick,
               copy_rates_range):
        setattr(m, fn.__name__, fn)
    return m


sys.modules["MetaTrader5"] = _make_mt5_module()

from app import mt5_client as mc  # noqa: E402


@pytest.fixture
def client():
    sys.modules["MetaTrader5"] = _make_mt5_module()
    FAKE.tick_times = {}
    mc._daily_cache.clear()  # 模块级 60s 缓存隔离
    c = mc.MT5Client()
    yield c
    asyncio.run(c.shutdown())


def test_skew_median_resists_pollution(client) -> None:
    """3 样本取中位数：XAUUSD 排首位（旧实现单样本会污染全局）也不影响。"""
    now = int(time.time())
    FAKE.tick_times = {
        "XAUUSD": now + SKEW + POLLUTION,
        "EURUSD": now + SKEW,
        "GBPUSD": now + SKEW,
    }
    out = asyncio.run(client.quotes(["XAUUSD", "EURUSD", "GBPUSD"]))
    assert len(out) == 3
    assert client._tz_skew == SKEW  # 全局 skew 取中位数，不被 +18min 污染
    for q in out:
        # EURUSD/GBPUSD 直接对齐；XAUUSD 残余 +18min 被 min(·, now) 钳回
        assert abs(q["bar_time"] - int(time.time())) <= 2


def test_bar_time_clamped_to_now(client) -> None:
    """skew 低估时污染 tick 的 bar_time 会超前 → 必须被钳回当前时刻。"""
    now = int(time.time())
    FAKE.tick_times = {"XAUUSD": now + SKEW + POLLUTION}
    client._tz_skew = SKEW
    client._tz_skew_at = time.time()  # TTL 内不重估
    (out,) = asyncio.run(client.quotes(["XAUUSD"]))
    assert out["bar_time"] <= int(time.time())


def test_skew_ttl_gates_refresh(client) -> None:
    """10 分钟 TTL 内不重估 skew，过期后重新估算。"""
    now = int(time.time())
    FAKE.tick_times = {"EURUSD": now + SKEW}
    client._tz_skew = 600
    client._tz_skew_at = time.time()
    asyncio.run(client.quotes(["EURUSD"]))
    assert client._tz_skew == 600  # TTL 内 → 不覆盖
    client._tz_skew_at = time.time() - mc.MT5Client._TZ_SKEW_TTL - 1
    asyncio.run(client.quotes(["EURUSD"]))
    assert client._tz_skew == SKEW  # TTL 过期 → 重估
