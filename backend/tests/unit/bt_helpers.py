"""Shared synthetic-bar / payload builders for the M4 engine tests."""

from __future__ import annotations

import numpy as np

from app.sandbox.payload import BacktestPayload, BarArrays

POINT = 0.00001
T0 = 1_760_000_400  # hour-aligned epoch seconds
SYMBOL_INFO = {
    "point": POINT,
    "digits": 5,
    "contract_size": 100000.0,
    "volume_step": 0.01,
    "volume_min": 0.01,
    "volume_max": 100.0,
}


def make_bars(n: int = 12, drift: float = 0.00005, base: float = 1.10000,
              spread: int = 20) -> BarArrays:
    times = T0 + np.arange(n, dtype="int64") * 3600
    closes = base + np.arange(n) * drift
    opens = closes - 0.00001
    highs = np.maximum(opens, closes) + 0.00002
    lows = np.minimum(opens, closes) - 0.00002
    return BarArrays(
        time=times,
        open=opens,
        high=highs,
        low=lows,
        close=closes,
        tick_volume=np.full(n, 100, dtype="int64"),
        spread=np.full(n, spread, dtype="int64"),
    )


def make_payload(source: str, mode: str = "standard", warmup: int = 0,
                 **kw) -> BacktestPayload:
    defaults: dict = {
        "task_id": "task-1",
        "strategy_id": "strat-1",
        "source_code": source,
        "execution_mode": mode,
        "symbol": "EURUSD",
        "timeframe": "H1",
        "tf_seconds": 3600,
        "initial_deposit": 10000.0,
        "leverage": 100,
        "spread_mode": "current",
        "spread_points": None,
        "spread_snapshot": 20.0,
        "commission_per_lot": 0.0,
        "slippage_points": None,
        "warmup_bars": warmup,
        "parameters": {},
        "seed": 1,
        "symbol_info": dict(SYMBOL_INFO),
        "callback_timeout_seconds": 5.0,
        "on_init_timeout_seconds": 60.0,
        "stop_out_level": 50.0,
        "log_limit": 5000,
    }
    defaults.update(kw)
    return BacktestPayload(**defaults)


BUY_ONCE = '''
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_bar(ctx, bar):
    if ctx.storage.get("done"):
        return
    ctx.storage["done"] = True
    r = ctx.buy("EURUSD", 0.1)
    ctx.log.info(f"BUY:{r.success}:{r.error_code}:{r.filled_price}")

def on_order(ctx, order):
    pass
'''
