"""Gateway tick-range tests — fake MetaTrader5 module, no real terminal.

Covers the M-Tick ``copy_ticks_range`` mapping: time_msc passthrough, the
seconds→ms fallback when the terminal omits the ``time_msc`` field, the
``limit`` cap, and the unknown-symbol 404 path.
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

# -- fake MetaTrader5 -----------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


ROWS = [
    {"time": 1_759_612_801, "time_msc": 1_759_612_801_234, "bid": 1.1,
     "ask": 1.10002, "last": 0.0, "volume": 1, "flags": 30},
    {"time": 1_759_612_802, "time_msc": 1_759_612_802_567, "bid": 1.10001,
     "ask": 1.10003, "last": 0.0, "volume": 2, "flags": 30},
]

FAKE = _NS(ticks=ROWS, ticks_without_msc=False)


def _make_mt5_module() -> types.ModuleType:
    import numpy as np

    m = types.ModuleType("MetaTrader5")
    m.__version__ = "5.0.0-fake"
    m.COPY_TICKS_ALL = 3  # 常量存在即可，fake 的 copy_ticks_range 忽略 flags

    def copy_ticks_range(symbol, date_from, date_to, flags=None):
        if FAKE.ticks_without_msc:
            names = ["time", "bid", "ask", "last", "volume", "flags"]
            dtype = [("time", "<i8"), ("bid", "<f8"), ("ask", "<f8"),
                     ("last", "<f8"), ("volume", "<i8"), ("flags", "<i4")]
        else:
            names = ["time", "bid", "ask", "last", "volume", "time_msc", "flags"]
            dtype = [("time", "<i8"), ("bid", "<f8"), ("ask", "<f8"),
                     ("last", "<f8"), ("volume", "<i8"), ("time_msc", "<i8"),
                     ("flags", "<i4")]
        arr = np.array(
            [tuple(r.get(n, 0) for n in names) for r in ROWS], dtype=dtype
        )
        return arr if ROWS else None

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
        return _NS(name=name, digits=5, point=0.00001, spread=20,
                   volume_min=0.01, volume_max=100.0, volume_step=0.01,
                   volume_digits=2, filling_mode=3, contract_size=100000.0)

    def symbol_info_tick(symbol):
        return _NS(time=0)  # time=0 → skew 保持 0，行时间戳不被平移

    for fn in (initialize, shutdown, last_error, terminal_info,
               symbol_select, symbol_info, symbols_total, symbol_info_tick,
               copy_ticks_range):
        setattr(m, fn.__name__, fn)
    return m


sys.modules["MetaTrader5"] = _make_mt5_module()

from app import mt5_client as mc  # noqa: E402


@pytest.fixture
def client():
    sys.modules["MetaTrader5"] = _make_mt5_module()
    c = mc.MT5Client()
    yield c
    _run(c.shutdown())


def _run(coro):
    return asyncio.run(coro)


def _range(start_s: int, end_s: int) -> tuple[datetime, datetime]:
    return (
        datetime.fromtimestamp(start_s, UTC),
        datetime.fromtimestamp(end_s, UTC),
    )


def test_ticks_range_maps_msc_fields(client) -> None:
    df, dt = _range(1_759_612_800, 1_759_613_800)
    out = _run(client.ticks_range("EURUSD", df, dt, 1000))
    assert len(out) == 2
    assert out[0]["time_msc"] == 1_759_612_801_234
    assert out[0]["bid"] == 1.1 and out[0]["ask"] == 1.10002
    assert out[0]["last"] == 0.0 and out[0]["volume"] == 1
    assert out[0]["flags"] == 30
    assert out[1]["time_msc"] == 1_759_612_802_567


def test_ticks_range_seconds_fallback_without_msc(client) -> None:
    FAKE.ticks_without_msc = True
    try:
        df, dt = _range(1_759_612_800, 1_759_613_800)
        out = _run(client.ticks_range("EURUSD", df, dt, 1000))
        assert [r["time_msc"] for r in out] == [
            1_759_612_801_000, 1_759_612_802_000,
        ]
    finally:
        FAKE.ticks_without_msc = False


def test_ticks_range_limit_cap_and_404(client) -> None:
    from app.mt5_client import MT5SymbolNotFound

    df, dt = _range(1_759_612_800, 1_759_613_800)
    out = _run(client.ticks_range("EURUSD", df, dt, 1))
    assert len(out) == 1  # capped

    with pytest.raises(MT5SymbolNotFound):
        _run(client.ticks_range("NOSUCH", df, dt, 1000))
