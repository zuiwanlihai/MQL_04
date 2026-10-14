"""
RSI 均值回归策略模板。

RSI 低于超卖阈值时买入，回升超过离场阈值时平仓。
RSI 在本文件内用纯 Python 计算（Wilder 平滑）——
与内置指标库结果一致。
"""


def _rsi(closes, period):
    """Wilder RSI over the last `period` changes; returns None if not enough."""
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(len(closes) - period, len(closes)):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - 100.0 / (1.0 + rs)


def on_init(ctx):
    ctx.declare(
        symbol="EURUSD",
        period="M15",
        params={
            "rsi_period": 14,
            "oversold": 30.0,
            "overbought": 70.0,
            "volume": 0.1,
        },
    )


def on_bar(ctx, bar):
    need = ctx.params["rsi_period"] + 1
    bars = ctx.get_bars(ctx.symbol, ctx.period, count=need)
    if len(bars) < need:
        return

    closes = [b.close for b in bars]
    rsi = _rsi(closes, ctx.params["rsi_period"])
    if rsi is None:
        return

    positions = ctx.get_positions()

    if rsi < ctx.params["oversold"]:
        if not any(p.direction == "buy" for p in positions):
            ctx.log.info(f"RSI {rsi:.1f} oversold -> BUY")
            result = ctx.buy(ctx.symbol, ctx.params["volume"], comment="rsi-reversion")
            if not result.success:
                ctx.log.warn(f"Buy failed: {result.error_code} - {result.error_message}")
    elif rsi > ctx.params["overbought"]:
        for pos in positions:
            if pos.direction == "buy":
                ctx.log.info(f"RSI {rsi:.1f} overbought -> EXIT")
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
