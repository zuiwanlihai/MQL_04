"""
MT5 Quant Platform - Strategy SDK v1

This is the SDK that strategy code imports. It provides:
- StrategyContext: the `ctx` object passed to strategy callbacks
- Built-in indicators: SMA, EMA, MACD, RSI, KDJ, BOLL, ATR, CCI, ADX, OBV
- Error codes: structured error enum shared between SDK and sandbox

Strategy skeleton:
    def on_init(ctx):
        ...
    def on_bar(ctx, bar):
        ...
    def on_order(ctx, order):
        ...
    def on_stop(ctx, reason):
        ...
    def on_tick(ctx, tick):   # paper/live only — never called in backtest
        ...
"""

__version__ = "1.1.0"