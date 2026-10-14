"""
网格交易策略模板（双向开仓）。

以锚定价为轴：下方逐格买入、上方逐格卖出开空；多仓涨一格平多、空仓跌一格平空。
网格间距以「点」为单位（点 = 券商最小报价单位 point，跨品种统一口径）：
EURUSD 200 点 = 0.0020，XAUUSD 200 点 = 2.00。
演示用 ``ctx.storage`` 保存必须跨重启存活的状态（网格锚定价、档位占用登记）。

风险提示（SDK v1 MVP）：券商不提供任何止损/止盈保护单 ——
策略崩溃或网关断线时，持仓处于无保护状态。出场逻辑就在本文件的 on_bar 中。
"""


def _grid_occupied(ctx, side, poss, anchor, step):
    """网格档位占用登记：返回当前已被占用的档位集合。

    实盘市价成交价可能远离信号档位价（点差 + 滑点 + 行情跑动），不能用
    「成交价 ≈ 目标价」判断档位是否已开 —— 旧版 step/10 容差在实盘永不命中，
    同一档位每根 K 线重复开仓直至满仓。改为在 ctx.storage 里持久化档位占用：

    - 下单成功 → 档位记入 grid_<side>_fired（待绑定），立刻阻止同档位重复下单；
    - 持仓出现在账户镜像后，与待绑定档位一对一配对（各自按价格/档位排序对齐），
      记入 grid_<side>_map（pos_id → level）；
    - 持仓消失（网格平仓 / 手动平仓 / 强平）即释放档位，允许再次开出；
    - 镜像滞后保护：持仓数 < 已登记数（含待绑定）说明下单后的仓位尚未出现在
      账户镜像里，保留待绑定档位，防止下一根 K 线重复开仓。

    无档位可配对的存量仓位（升级 / 重启后 storage 缺档位登记）按最低空闲档位
    登记，保证升级后不重复开仓。
    """
    fired_key = "grid_" + side + "_fired"
    map_key = "grid_" + side + "_map"
    fired = list(ctx.storage.get(fired_key, []))
    slot = dict(ctx.storage.get(map_key, {}))

    live_ids = set()
    for p in poss:
        live_ids.add(p.id)
    # 持仓已消失（网格平仓 / 手动平仓 / 强平）→ 释放其档位
    slot = {k: v for k, v in slot.items() if k in live_ids}

    bound = set()
    for v in slot.values():
        bound.add(v)
    pending = []
    for lv in fired:
        if lv not in bound:
            pending.append(lv)

    # 一对一配对：未建档持仓 ↔ 待绑定档位。网格按档位顺序开仓：卖出档位
    # 越高开仓价越高，买入档位越高开仓价越低，故持仓按开仓价升序、档位按
    # 对应价格顺序排列后逐一对齐即可正确配对（buy 需要档位倒序）。
    unbound = []
    for p in poss:
        if p.id not in slot:
            unbound.append(p)
    if unbound and pending:
        pos_sorted = sorted(unbound, key=lambda p: p.open_price)
        lv_sorted = sorted(pending, reverse=(side == "buy"))
        n = min(len(pos_sorted), len(lv_sorted))
        for i in range(n):
            slot[pos_sorted[i].id] = lv_sorted[i]
            bound.add(lv_sorted[i])
        pending = []
        for lv in fired:
            if lv not in bound:
                pending.append(lv)

    # 无档位可配对的存量仓位 → 占用最低空闲档位（自愈登记，防重复开仓）
    unbound = []
    for p in poss:
        if p.id not in slot:
            unbound.append(p)
    for p in unbound:
        lv = 1
        while lv in bound:
            lv += 1
        slot[p.id] = lv
        bound.add(lv)
        if lv in pending:
            pending.remove(lv)

    # 镜像滞后保护（见 docstring）：仓位尚未出现在镜像时保留 pending
    if len(poss) >= len(slot) + len(pending):
        pending = []

    ctx.storage[fired_key] = pending
    ctx.storage[map_key] = slot

    occupied = set(bound)
    for lv in pending:
        occupied.add(lv)
    return occupied


def on_init(ctx):
    ctx.declare(
        symbol="EURUSD",
        period="M5",
        params={
            "grid_step": 200,        # 网格间距（点）：EURUSD 200点=0.0020，XAUUSD 200点=2.00
            "max_levels": 5,         # max grid entries PER SIDE (buy / sell)
            "volume": 0.1,
        },
    )
    # Restore the anchor across restarts; plain variables are NOT recovered.
    if ctx.storage.get("anchor") is None:
        quote = ctx.get_quote(ctx.symbol)
        if quote is not None:
            ctx.storage["anchor"] = quote.bid


def on_bar(ctx, bar):
    info = ctx.get_symbol_info(ctx.symbol)
    point = info.point if info else 0.0
    if point <= 0:
        return  # 尚无品种信息，无法把点换算成价格距离
    step = ctx.params["grid_step"] * point
    max_levels = ctx.params["max_levels"]
    volume = ctx.params["volume"]

    anchor = ctx.storage.get("anchor")
    if anchor is None:
        quote = ctx.get_quote(ctx.symbol)
        if quote is not None:
            ctx.storage["anchor"] = quote.bid
        return

    positions = ctx.get_positions()
    buy_poss = [p for p in positions if p.direction == "buy"]
    sell_poss = [p for p in positions if p.direction == "sell"]

    # Exit: long closed one grid step above its entry, short one step below.
    for pos in positions:
        if pos.direction == "buy" and bar.close >= pos.open_price + step:
            ctx.log.info(f"GRID exit-long: close {pos.id} at {bar.close:.5f}")
            result = ctx.close(pos.id)
            if not result.success:
                ctx.log.warn(f"Close failed: {result.error_code} - {result.error_message}")
        elif pos.direction == "sell" and bar.close <= pos.open_price - step:
            ctx.log.info(f"GRID exit-short: close {pos.id} at {bar.close:.5f}")
            result = ctx.close(pos.id)
            if not result.success:
                ctx.log.warn(f"Close failed: {result.error_code} - {result.error_message}")

    # Entry (long): buy one level below anchor when that level is not occupied.
    # 档位占用去重：已开且未释放的档位不再开 —— 实盘成交价偏离目标价也不会重复开仓
    occupied = _grid_occupied(ctx, "buy", buy_poss, anchor, step)
    if len(buy_poss) < max_levels:
        for level in range(1, max_levels + 1):
            target = anchor - level * step
            if bar.close <= target and level not in occupied:
                ctx.log.info(f"GRID buy: level {level} @ {target:.5f}")
                result = ctx.buy(ctx.symbol, volume, comment=f"grid-buy-{level}")
                if result.success:
                    fired = list(ctx.storage.get("grid_buy_fired", []))
                    fired.append(level)
                    ctx.storage["grid_buy_fired"] = fired
                else:
                    ctx.log.warn(f"Buy failed: {result.error_code} - {result.error_message}")
                break

    # Entry (short): sell one level above anchor when that level is not occupied.
    occupied = _grid_occupied(ctx, "sell", sell_poss, anchor, step)
    if len(sell_poss) < max_levels:
        for level in range(1, max_levels + 1):
            target = anchor + level * step
            if bar.close >= target and level not in occupied:
                ctx.log.info(f"GRID sell: level {level} @ {target:.5f}")
                result = ctx.sell(ctx.symbol, volume, comment=f"grid-sell-{level}")
                if result.success:
                    fired = list(ctx.storage.get("grid_sell_fired", []))
                    fired.append(level)
                    ctx.storage["grid_sell_fired"] = fired
                else:
                    ctx.log.warn(f"Sell failed: {result.error_code} - {result.error_message}")
                break


def on_order(ctx, order):
    if order.success:
        ctx.log.info(
            f"Order filled: {order.order_id} volume={order.filled_volume} "
            f"price={order.filled_price}"
        )


def on_stop(ctx, reason):
    ctx.log.info(f"Strategy stopped: {reason} (grid state kept in storage)")
