"""
ATR 移动止损仓位管理模板。

快/慢均线金叉进场，通过 ctx.storage 跟踪的 ATR 移动止损离场。
重要提示（SDK v1 MVP）：不会向券商提交任何止损单 ——
移动止损由本策略每根 K 线自行执行。若进程崩溃，持仓将没有保护；
此模式必须配合平台级风控规则（M6）一起使用。
"""


def _atr(bars, period):
    """Average True Range (simple mean of TR) over `period` closed bars."""
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(len(bars) - period, len(bars)):
        prev_close = bars[i - 1].close
        tr = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - prev_close),
            abs(bars[i].low - prev_close),
        )
        trs.append(tr)
    return sum(trs) / len(trs)


def on_init(ctx):
    ctx.declare(
        symbol="GBPUSD",
        period="H1",
        params={
            "fast_period": 10,
            "slow_period": 30,
            "atr_period": 14,
            "atr_mult": 2.0,
            "volume": 0.1,
        },
    )


def on_bar(ctx, bar):
    fast = ctx.params["fast_period"]
    slow = ctx.params["slow_period"]
    atr_period = ctx.params["atr_period"]
    need = max(slow, atr_period) + 1

    bars = ctx.get_bars(ctx.symbol, ctx.period, count=need)
    if len(bars) < need:
        return

    closes = [b.close for b in bars]
    fast_ma = sum(closes[-fast:]) / fast
    slow_ma = sum(closes[-slow:]) / slow
    atr = _atr(bars, atr_period)
    if atr is None:
        return

    stop = ctx.storage.get("stop_price")
    positions = ctx.get_positions()

    # 1) Trailing-stop exit (strategy-side, NOT a broker protection order).
    for pos in positions:
        if pos.direction == "buy":
            if stop is not None and bar.close <= stop:
                ctx.log.info(f"ATR stop hit: {bar.close:.5f} <= {stop:.5f} -> EXIT")
                result = ctx.close(pos.id)
                if not result.success:
                    ctx.log.warn(f"Close failed: {result.error_code} - {result.error_message}")
                else:
                    ctx.storage["stop_price"] = None
            else:
                # Trail the stop up as price rises.
                candidate = bar.close - ctx.params["atr_mult"] * atr
                if stop is None or candidate > stop:
                    ctx.storage["stop_price"] = candidate

    # 2) Entry on golden cross.
    prev_fast = sum(closes[-fast - 1:-1]) / fast
    prev_slow = sum(closes[-slow - 1:-1]) / slow
    if prev_fast <= prev_slow and fast_ma > slow_ma:
        if not any(p.direction == "buy" for p in positions):
            ctx.log.info(f"Golden cross -> BUY, initial stop {bar.close - ctx.params['atr_mult'] * atr:.5f}")
            result = ctx.buy(ctx.symbol, ctx.params["volume"], comment="atr-stop")
            if not result.success:
                ctx.log.warn(f"Buy failed: {result.error_code} - {result.error_message}")
            else:
                ctx.storage["stop_price"] = bar.close - ctx.params["atr_mult"] * atr


def on_order(ctx, order):
    if order.success:
        ctx.log.info(
            f"Order filled: {order.order_id} "
            f"volume={order.filled_volume} price={order.filled_price}"
        )


def on_stop(ctx, reason):
    ctx.log.info(f"Strategy stopped: {reason}")
