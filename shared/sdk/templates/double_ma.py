"""
双均线交叉策略模板。

经典趋势跟踪策略：
- 快线上穿慢线时买入
- 快线下穿慢线时卖出
- 同一时间仅持有一单
"""


def on_init(ctx):
    """Initialize strategy parameters and symbol declarations."""
    ctx.declare(
        symbol="EURUSD",
        period="H1",
        params={
            "fast_period": 5,
            "slow_period": 20,
            "volume": 0.1,
        },
    )


def on_bar(ctx, bar):
    """Process each completed bar."""
    # Get historical bars for indicator calculation
    bars = ctx.get_bars(ctx.symbol, ctx.period, count=ctx.params["slow_period"] + 1)
    if len(bars) < ctx.params["slow_period"] + 1:
        return

    closes = [b.close for b in bars]

    # Calculate moving averages
    fast_ma = sum(closes[-ctx.params["fast_period"]:]) / ctx.params["fast_period"]
    slow_ma = sum(closes[-ctx.params["slow_period"]:]) / ctx.params["slow_period"]

    prev_fast = sum(closes[-ctx.params["fast_period"] - 1:-1]) / ctx.params["fast_period"]
    prev_slow = sum(closes[-ctx.params["slow_period"] - 1:-1]) / ctx.params["slow_period"]

    positions = ctx.get_positions()

    # Buy signal: fast crosses above slow
    if prev_fast <= prev_slow and fast_ma > slow_ma:
        if not positions:
            ctx.log.info(f"BUY signal: fast_ma={fast_ma:.5f} > slow_ma={slow_ma:.5f}")
            result = ctx.buy(ctx.symbol, ctx.params["volume"])
            if not result.success:
                ctx.log.warn(f"Buy failed: {result.error_code} - {result.error_message}")

    # Sell signal: fast crosses below slow
    elif prev_fast >= prev_slow and fast_ma < slow_ma:
        for pos in positions:
            if pos.direction == "buy":
                ctx.log.info(f"SELL signal: close long position")
                result = ctx.close(pos.id)
                if not result.success:
                    ctx.log.warn(f"Close failed: {result.error_code} - {result.error_message}")


def on_order(ctx, order):
    """Handle order fill notifications."""
    if order.success:
        ctx.log.info(
            f"Order filled: {order.order_id} "
            f"volume={order.filled_volume} price={order.filled_price}"
        )


def on_stop(ctx, reason):
    """Cleanup when strategy is stopped."""
    ctx.log.info(f"Strategy stopped: {reason}")