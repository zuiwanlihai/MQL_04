"""Gateway history probe/backfill tests — fake MetaTrader5, no terminal.

Covers the M2 数据中心 mapping: probe 分页向历史回溯（短读=本地最早）、
truncated 上限、maxbars 玻璃天花板、空序列、未知品种 404；backfill 的
收敛判定（first<=start → complete / 两轮签名一致提前 break）与 attempts 统计。
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


T0 = 1_700_000_000

# pos_bars: copy_rates_from_pos 的「终端本地缓存」模拟（ascending 旧→新）
# range_provider: copy_rates_range 编程式返回（按调用次数模拟补数收敛）
FAKE = _NS(pos_bars=None, range_provider=None, range_calls=0,
           ipc_alive=True, initialize_calls=0)

_DTYPE = [
    ("time", "<i8"),
    ("open", "<f8"),
    ("high", "<f8"),
    ("low", "<f8"),
    ("close", "<f8"),
    ("tick_volume", "<i8"),
    ("spread", "<i4"),
    ("real_volume", "<i8"),
]


def _bars(n: int, t0: int = T0, step: int = 60) -> list[dict]:
    return [
        {"time": t0 + i * step, "open": 1.1, "high": 1.2, "low": 1.0,
         "close": 1.15, "tick_volume": 100, "spread": 1, "real_volume": 0}
        for i in range(n)
    ]


def _arr(rows: list[dict] | None):
    import numpy as np

    if not rows:
        return None
    return np.array(
        [tuple(r[name] for name, _ in _DTYPE) for r in rows], dtype=_DTYPE
    )


def _make_mt5_module() -> types.ModuleType:
    m = types.ModuleType("MetaTrader5")
    m.__version__ = "5.0.0-fake"

    def copy_rates_from_pos(symbol, timeframe, start_pos, count):
        bars = FAKE.pos_bars or []
        n = len(bars)
        if count >= 1000:
            return None  # 真实终端行为：count == maxbars → Invalid params 空结果
        if start_pos >= n:
            return None  # 超出本地序列起点
        avail = n - start_pos
        take = min(count, avail)
        # position p（自最新向历史数）对应 ascending 序列中的 t[n-1-p]
        return _arr(bars[n - start_pos - take: n - start_pos])

    def copy_rates_range(symbol, timeframe, date_from, date_to):
        FAKE.range_calls += 1
        if FAKE.range_provider is None:
            return None
        return _arr(FAKE.range_provider(FAKE.range_calls))

    def initialize(**kw):
        FAKE.initialize_calls += 1
        return True

    def shutdown():
        return None

    def last_error():
        return (1, "fake")

    def terminal_info():
        # 终端 IPC 楔死时仍返回非 None（实测陈旧缓存），symbols_total 才是真探针
        return _NS(connected=True, trade_allowed=True, maxbars=1000)

    def symbols_total():
        return 10 if FAKE.ipc_alive else None

    def symbol_select(name, enable):
        return name != "NOSUCH"

    def symbol_info(name):
        if name == "NOSUCH":
            return None
        return _NS(name=name, digits=5, point=0.00001, spread=20,
                   volume_min=0.01, volume_max=100.0, volume_step=0.01,
                   volume_digits=2, filling_mode=3, contract_size=100000.0)

    for fn in (initialize, shutdown, last_error, terminal_info,
               symbol_select, symbol_info, symbols_total,
               copy_rates_from_pos, copy_rates_range):
        setattr(m, fn.__name__, fn)
    return m


sys.modules["MetaTrader5"] = _make_mt5_module()

from app import mt5_client as mc  # noqa: E402


@pytest.fixture
def client():
    sys.modules["MetaTrader5"] = _make_mt5_module()
    # 缩小分页/上限便于测试；记录原值供恢复（settings 是会话级单例）
    orig = {
        k: getattr(mc.settings, k)
        for k in ("HISTORY_PROBE_PAGE_SIZE", "HISTORY_PROBE_MAX_BARS",
                  "HISTORY_BACKFILL_RETRY_INTERVAL", "RATES_TAIL_HEAL_INTERVAL")
    }
    mc.settings.HISTORY_PROBE_PAGE_SIZE = 10
    mc.settings.HISTORY_PROBE_MAX_BARS = 500
    mc.settings.HISTORY_BACKFILL_RETRY_INTERVAL = 0.01
    mc.settings.RATES_TAIL_HEAL_INTERVAL = 0.01
    FAKE.pos_bars = None
    FAKE.range_provider = None
    FAKE.range_calls = 0
    FAKE.ipc_alive = True
    FAKE.initialize_calls = 0
    c = mc.MT5Client()
    yield c
    asyncio.run(c.shutdown())
    for k, v in orig.items():
        setattr(mc.settings, k, v)


# -- probe ----------------------------------------------------------------------

def test_probe_full_series(client) -> None:
    FAKE.pos_bars = _bars(35)  # page=10 → 10+10+10+5 短读终止
    out = asyncio.run(client.history_probe("EURUSD", "H1"))
    assert out["symbol"] == "EURUSD" and out["timeframe"] == "H1"
    assert out["bars_count"] == 35
    assert out["first_bar_time"] == T0
    assert out["last_bar_time"] == T0 + 34 * 60
    assert out["truncated"] is False
    assert out["max_bars"] == 1000


def test_probe_empty(client) -> None:
    out = asyncio.run(client.history_probe("EURUSD", "H1"))
    assert out["bars_count"] == 0
    assert out["first_bar_time"] is None and out["last_bar_time"] is None
    assert out["truncated"] is False


def test_probe_truncated(client) -> None:
    mc.settings.HISTORY_PROBE_MAX_BARS = 12
    FAKE.pos_bars = _bars(35)
    out = asyncio.run(client.history_probe("EURUSD", "H1"))
    assert out["truncated"] is True
    assert out["bars_count"] == 20  # 10+10 后达到上限
    assert out["first_bar_time"] == T0 + 15 * 60


def test_probe_unknown_symbol(client) -> None:
    from app.mt5_client import MT5SymbolNotFound

    with pytest.raises(MT5SymbolNotFound):
        asyncio.run(client.history_probe("NOSUCH", "H1"))


def test_probe_page_clamped_below_maxbars(client) -> None:
    """PAGE_SIZE == maxbars：page 必须 clamp 到 maxbars-1，否则终端
    对 count == maxbars 返回 Invalid params 空结果（5.0.6231 实测）。"""
    mc.settings.HISTORY_PROBE_PAGE_SIZE = 1000  # == fake terminal maxbars
    mc.settings.HISTORY_PROBE_MAX_BARS = 5000
    FAKE.pos_bars = _bars(1500)
    out = asyncio.run(client.history_probe("EURUSD", "H1"))
    assert out["bars_count"] == 1500
    assert out["first_bar_time"] == T0
    assert out["truncated"] is False


# -- backfill -------------------------------------------------------------------

def test_backfill_converges_complete(client) -> None:
    # None → 部分（first > start）→ 全量（first <= start）：3 轮收敛
    part = _bars(5, t0=T0 + 10 * 60)
    full = _bars(20, t0=T0)

    def provider(calls: int):
        if calls == 1:
            return None
        if calls == 2:
            return part
        return full

    FAKE.range_provider = provider
    out = asyncio.run(
        client.history_backfill("EURUSD", "H1", T0, T0 + 30 * 60, max_attempts=6)
    )
    assert out["attempts"] == 3
    assert out["complete"] is True
    assert out["first_bar_time"] == T0
    assert out["bars_count"] == 20
    assert out["requested_start"] == T0


def test_backfill_signature_break(client) -> None:
    # 恒定部分数据：第二轮签名与第一轮一致 → 提前 break（终端已尽力）
    rows = _bars(5, t0=T0 + 10 * 60)
    FAKE.range_provider = lambda calls: rows
    out = asyncio.run(
        client.history_backfill("EURUSD", "H1", T0, T0 + 30 * 60, max_attempts=6)
    )
    assert out["attempts"] == 2
    assert out["complete"] is False


def test_backfill_first_attempt_complete(client) -> None:
    FAKE.range_provider = lambda calls: _bars(20, t0=T0)
    out = asyncio.run(
        client.history_backfill("EURUSD", "H1", T0, T0 + 30 * 60, max_attempts=3)
    )
    assert out["attempts"] == 1
    assert out["complete"] is True


# -- rates 尾部滞后自愈 ------------------------------------------------------------

def _recent_bars(n: int, tf: int, end_bucket: int | None = None) -> list[dict]:
    """n 根连续 bar（ascending），尾根对齐到 end_bucket（默认当前桶起点）。"""
    last = end_bucket if end_bucket is not None else int(time.time()) // tf * tf
    return [
        {"time": last - (n - 1 - i) * tf, "open": 1.1, "high": 1.2, "low": 1.0,
         "close": 1.15, "tick_volume": 100, "spread": 1, "real_volume": 0}
        for i in range(n)
    ]


def test_rates_fresh_no_retry(client) -> None:
    FAKE.range_provider = lambda calls: _recent_bars(5, 60)
    rows = asyncio.run(client.rates("EURUSD", "M1", 200))
    assert FAKE.range_calls == 1  # 新鲜 → 不触发自愈重试
    assert rows[-1]["time"] >= int(time.time()) - 120


def test_rates_tail_heal_recovers(client) -> None:
    tf = 60
    stale = _recent_bars(5, tf, int(time.time()) // tf * tf - 3 * tf)
    fresh = _recent_bars(5, tf)
    FAKE.range_provider = lambda calls: fresh if calls > 1 else stale
    rows = asyncio.run(client.rates("EURUSD", "M1", 200))
    assert FAKE.range_calls >= 2  # 首轮陈旧 → 触发自愈重试拉齐
    assert rows[-1]["time"] >= int(time.time()) // tf * tf - tf


def test_rates_fresh_on_offset_axis_no_retry(client) -> None:
    """bars 轴与 time.time() 有固定偏移（broker 服务器伪 epoch，demo 实测
    -28800s）时，新鲜度判定必须走报价换算的轴提示，否则轴上新鲜的尾根被
    误判为滞后、每 5 分钟空转一轮重试+冷却。"""
    tf = 60
    axis_now = int(time.time()) - 28800
    client._axis_hint["EURUSD"] = (axis_now, time.time())
    FAKE.range_provider = lambda calls: _recent_bars(5, tf, axis_now // tf * tf)
    rows = asyncio.run(client.rates("EURUSD", "M1", 200))
    assert FAKE.range_calls == 1  # 轴上新鲜 → 不重试
    assert rows[-1]["time"] >= axis_now // tf * tf - tf


def test_rates_tail_heal_cooldown(client) -> None:
    tf = 60
    # 永远落后 5 根 → 自愈不收敛（休市场景）
    FAKE.range_provider = (
        lambda calls: _recent_bars(5, tf, int(time.time()) // tf * tf - 5 * tf)
    )
    asyncio.run(client.rates("EURUSD", "M1", 200))
    assert FAKE.range_calls == 3  # 首次 + 默认 2 次自愈重试
    asyncio.run(client.rates("EURUSD", "M1", 200))
    assert FAKE.range_calls == 4  # 冷却期内不再重试


def test_rates_reinit_after_terminal_wedge(client) -> None:
    """终端被杀后 terminal_info 返回陈旧缓存 → symbols_total 探针失败 → 强制重连。"""
    FAKE.range_provider = lambda calls: _recent_bars(5, 60)
    asyncio.run(client.rates("EURUSD", "M1", 200))
    assert FAKE.initialize_calls == 1  # 首次懒初始化
    FAKE.ipc_alive = False  # 模拟终端死亡（IPC 楔死，terminal_info 仍非 None）
    asyncio.run(client.rates("EURUSD", "M1", 200))
    assert FAKE.initialize_calls == 2  # 探针失败 → shutdown + 重新 initialize
    FAKE.ipc_alive = True
    asyncio.run(client.rates("EURUSD", "M1", 200))
    assert FAKE.initialize_calls == 2  # 已重新附着，不再重复初始化
