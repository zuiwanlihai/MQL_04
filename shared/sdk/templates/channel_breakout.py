"""
唐奇安通道突破策略模板。

趋势跟踪：收盘价突破前 N 根最高价时买入，
跌破前 M 根最低价时离场。纯 Python 实现，不依赖 numpy，直接使用 K 线列表。
"""


def on_init(ctx):
    ctx.declare(
        symbol="XAUUSD",
        period="H1",
        params={
            "entry_window": 20,   # Donchian lookback for entries
            "exit_window": 10,    # shorter lookback for exits
            "volume": 0.1,
        },
    )


def on_bar(ctx, bar):
    entry_window = ctx.params["entry_window"]
    exit_window = ctx.params["exit_window"]
    need = max(entry_window, exit_window) + 1

    bars = ctx.get_bars(ctx.symbol, ctx.period, count=need)
    if len(bars) < need:
        return  # warm-up: on_bar still fires, but orders would be rejected

    window = bars[:-1]  # exclude the current bar (it just closed)
    entry_high = max(b.high for b in window[-entry_window:])
    exit_low = min(b.low for b in window[-exit_window:])

    positions = ctx.get_positions()

    if bar.close > entry_high:
        if not any(p.direction == "buy" for p in positions):
            ctx.log.info(f"BREAKOUT long: close {bar.close:.5f} > high {entry_high:.5f}")
            result = ctx.buy(ctx.symbol, ctx.params["volume"], comment="donchian")
            if not result.success:
                ctx.log.warn(f"Buy failed: {result.error_code} - {result.error_message}")
    elif bar.close < exit_low:
        for pos in positions:
            if pos.direction == "buy":
                ctx.log.info(f"EXIT: close {bar.close:.5f} < low {exit_low:.5f}")
                result = ctx.close(pos.id)
                if not result.success:
                    ctx.log.warn(f"Close failed: {result.error_code} - {result.error_message}")


def on_order(ctx, order):
    if order.success:
        ctx.log.info(
            f"Order filled: {order.order_id} "
            f"volume={order.filled_volume} price={order.filled_price}"
        )


def on_stop(ctx, reason):
    ctx.log.info(f"Strategy stopped: {reason}")
