"""
BOLL+MACD+MA 融合策略 v4.0（完整版: MVP + P1 + P2）.

依据 BOLL_MACD_MA_4.0.md 方案的轨道 B 落地：

门槛层(Gate):
  G1 趋势方向: EMA200 + 斜率(非对称)
  G2 交易许可: 时段过滤(DST 自适应) + 熔断 + 相关性品种同向敞口

评分层(Score, 满分 100, 总分 >= min_score 开仓, [shadow_min, min_score) 记影子):
  S1 位置 0~30   (必要 >=20): 下/上轨收回 或 站上/下中轨
  S2 动量 -15~+25: MACD 交叉(3根窗口) + 零轴加成 + 柱状线加成
  S3 波动 0~15   : 带宽 > SMA(带宽)(非对称回看, 软化得分项)
  S4 形态 -20~+15: 连续强度评分(锤子/流星线、吞没、启明/黄昏星、影线否决)
  S5 背离 0~+15  : 顶底背离(20周期窗口, 前极值间隔 >=5 根防伪)

风控层:
  四级熔断: 连亏(3->4h, 5->24h, 恢复首笔减半) / 日亏3% / 周亏6% / 总回撤15%停机
  波动率自适应仓位: ATR(14) 百分位 >80% -> x0.70, <30% -> x1.10
  时段降仓: 纽约尾盘(重叠结束后~21:00 GMT) x0.50
  资金曲线斜率: 30样本回归斜率持续为负 -> 降仓, 更久 -> 人工复查标记
  相关性过滤: corr_symbols 列表品种存在同向持仓时拒绝新开仓

出场层:
  结构止损(轨外 ± ATR, 非对称) + 中轨分批止盈(50%/60%)
  + 四阶段动态跟踪线 + 时间止损(8/6 根)

数据层(AI 就绪):
  平仓特征与影子信号经 ctx.storage 有界记录(默认各 500 条),
  平台成交记录为权威特征源; 新闻过滤待平台日历接口就绪后启用。

时序约定: 全部使用已收盘 K 线 —— bars[-1] 即文档的 bar[1]，bars[-2] 即 bar[2]，
指标值与价格一律同 bar 对齐，杜绝未来函数。止损为收盘级(L0)执行，无券商保护单。
状态(熔断/连亏/跟踪阶段/净值历史/特征)经 ctx.storage 持久化，跨重启恢复。
"""

from datetime import datetime, timedelta

# ── 内置指标(纯 Python，自包含) ────────────────────────────────


def _ema(series, period):
    out = [float("nan")] * len(series)
    if len(series) < period:
        return out
    out[period - 1] = sum(series[:period]) / period
    k = 2.0 / (period + 1)
    for i in range(period, len(series)):
        out[i] = series[i] * k + out[i - 1] * (1 - k)
    return out


def _sma(series, period):
    out = [float("nan")] * len(series)
    if len(series) < period:
        return out
    s = sum(series[:period])
    out[period - 1] = s / period
    for i in range(period, len(series)):
        s += series[i] - series[i - period]
        out[i] = s / period
    return out


def _boll(closes, period, dev):
    """返回 (upper, middle, lower)，总体标准差。"""
    n = len(closes)
    up, mid, lo = [float("nan")] * n, [float("nan")] * n, [float("nan")] * n
    if n < period:
        return up, mid, lo
    s = sum(closes[:period])
    sq = sum(x * x for x in closes[:period])
    for i in range(period - 1, n):
        if i > period - 1:
            s += closes[i] - closes[i - period]
            sq += closes[i] ** 2 - closes[i - period] ** 2
        m = s / period
        var = max(sq / period - m * m, 0.0)
        sd = var ** 0.5
        mid[i] = m
        up[i] = m + dev * sd
        lo[i] = m - dev * sd
    return up, mid, lo


def _atr(highs, lows, closes, period):
    """Wilder ATR。"""
    n = len(closes)
    out = [float("nan")] * n
    if n < period:
        return out
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    out[period - 1] = sum(trs[:period]) / period
    for i in range(period, n):
        out[i] = (out[i - 1] * (period - 1) + trs[i]) / period
    return out


def _macd(closes, fast, slow, signal):
    """返回 (main, signal, hist)。"""
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    n = len(closes)
    main = [float("nan")] * n
    for i in range(n):
        if ef[i] == ef[i] and es[i] == es[i]:
            main[i] = ef[i] - es[i]
    start = slow - 1
    sig = [float("nan")] * n
    if n - start >= signal:
        sig_valid = _ema(main[start:], signal)
        for i in range(start, n):
            sig[i] = sig_valid[i - start]
    hist = [
        (main[i] - sig[i]) if (main[i] == main[i] and sig[i] == sig[i]) else float("nan")
        for i in range(n)
    ]
    return main, sig, hist


# ── 工具 ───────────────────────────────────────────────────────

_PERIOD_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400,
}


def _clamp01(x):
    return max(0.0, min(1.0, x))


def _iso(t):
    return t.isoformat()


def _from_iso(s):
    return datetime.fromisoformat(s)


# ── 夏令时与时段过滤(P1: DST 自适应) ───────────────────────────


def _last_sunday_day(year, month):
    """该月最后一个周日的日期号。"""
    nxt = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    return (d - timedelta(days=(d.weekday() + 1) % 7)).day


def _nth_sunday_day(year, month, n):
    """该月第 n 个周日的日期号。"""
    d = datetime(year, month, 1)
    first = 1 + (6 - d.weekday()) % 7
    return first + (n - 1) * 7


def _eu_dst(t):
    """欧洲夏令时: 3月最后周日 ~ 10月最后周日。"""
    return (
        datetime(t.year, 3, _last_sunday_day(t.year, 3))
        <= t
        < datetime(t.year, 10, _last_sunday_day(t.year, 10))
    )


def _us_dst(t):
    """美国夏令时: 3月第2个周日 ~ 11月第1个周日。"""
    return (
        datetime(t.year, 3, _nth_sunday_day(t.year, 3, 2))
        <= t
        < datetime(t.year, 11, _nth_sunday_day(t.year, 11, 1))
    )


def _session_state(t):
    """G2 时段过滤(UTC): 返回 'block' | 'caution' | 'ok'。

    伦敦/纽约时段边界随两地 DST 动态平移(重叠窗口即最佳交易时段);
    重叠结束后至 21:00 为纽约尾盘(caution, 降仓 50%);
    周五 20:00 后与周末禁止新开仓。
    """
    wd = t.weekday()  # Mon=0 .. Sun=6
    if wd == 5 or wd == 6:
        return "block"
    if wd == 4 and t.hour >= 20:
        return "block"
    lon_s, lon_e = 7 - _eu_dst(t), 16 - _eu_dst(t)      # 伦敦时段(GMT)
    ny_s, ny_e = 12 - _us_dst(t), 21 - _us_dst(t)       # 纽约时段(GMT)
    ovl_s, ovl_e = max(lon_s, ny_s), min(lon_e, ny_e)   # 重叠=最佳
    h = t.hour + t.minute / 60.0
    if ovl_e <= h < 21:
        return "caution"
    return "ok"


# ── K线形态连续强度(P2) ────────────────────────────────────────


def _pattern_score(bars3, is_long):
    """信号 K 线(bars3[-1])的形态连续强度评分, 范围约 -20 ~ +15。

    镜像原则: 多头确认=长下影(锤子线)/看涨吞没/启明星, 否决=长上影;
              空头确认=长上影(流星线)/看跌吞没/黄昏星, 否决=长下影。
    (v3.0 文档空头行同时列出上吊线与下影否决存在歧义, 此处按严格镜像实现)
    """
    b3, b2, b1 = bars3[-3], bars3[-2], bars3[-1]
    eps = 1e-12
    body1 = abs(b1.close - b1.open)
    body2 = abs(b2.close - b2.open)
    body3 = abs(b3.close - b3.open)
    lo_sh = min(b1.open, b1.close) - b1.low    # 下影
    up_sh = b1.high - max(b1.open, b1.close)   # 上影

    if is_long:
        # 锤子线强度: 下影/实体, 3 倍封顶
        score = _clamp01(lo_sh / (body1 + eps) / 3.0) * 15
        # 看涨吞没: b1 阳线实体覆盖 b2 阴线实体
        if b1.close > b1.open and b2.close < b2.open and b1.close > b2.open:
            score = max(score, _clamp01(body1 / (body2 + eps) / 1.5) * 15)
        # 启明星: b3 阴线 + b2 小实体 + b1 阳线收复 b3 实体中点上方
        if (b3.close < b3.open and body2 < body3 * 0.4
                and b1.close > b1.open and b1.close > (b3.open + b3.close) / 2):
            score = max(score, 12.0)
        # 上影否决(连续): 上影>实体 时按比例罚分
        u = up_sh / (body1 + eps)
        if u > 1:
            score -= 15 * min(u / 2.0, 1.5) / 1.5
    else:
        # 流星线强度: 上影/实体, 3 倍封顶
        score = _clamp01(up_sh / (body1 + eps) / 3.0) * 15
        # 看跌吞没: b1 阴线实体覆盖 b2 阳线实体
        if b1.close < b1.open and b2.close > b2.open and b1.close < b2.open:
            score = max(score, _clamp01(body1 / (body2 + eps) / 1.5) * 15)
        # 黄昏星: b3 阳线 + b2 小实体 + b1 阴线跌破 b3 实体中点下方
        if (b3.close > b3.open and body2 < body3 * 0.4
                and b1.close < b1.open and b1.close < (b3.open + b3.close) / 2):
            score = max(score, 12.0)
        # 下影否决(连续)
        d = lo_sh / (body1 + eps)
        if d > 1:
            score -= 15 * min(d / 2.0, 1.5) / 1.5
    return int(round(score))


def _divergence_score(lows, highs, m_main, is_long, window=20, gap=5):
    """背离检测(P2): 20 周期新极值 + MACD 未创新极值, 前极值间隔 >= gap 根。"""
    n = len(lows)
    if n < window + 1:
        return 0
    if is_long:
        if lows[-1] != min(lows[-window:]):
            return 0
        zone = lows[-window:-gap]
        if not zone:
            return 0
        prev_min = min(zone)
        if prev_min <= lows[-1]:  # 当前非相对新低
            return 0
        prev_idx = n - window + zone.index(prev_min)
        if m_main[-1] != m_main[-1] or m_main[prev_idx] != m_main[prev_idx]:
            return 0
        return 15 if m_main[-1] > m_main[prev_idx] else 0
    zone = highs[-window:-gap]
    if not zone or highs[-1] != max(highs[-window:]):
        return 0
    prev_max = max(zone)
    if prev_max >= highs[-1]:  # 当前非相对新高
        return 0
    prev_idx = n - window + zone.index(prev_max)
    if m_main[-1] != m_main[-1] or m_main[prev_idx] != m_main[prev_idx]:
        return 0
    return 15 if m_main[-1] < m_main[prev_idx] else 0


# ── 数据层(AI 就绪): 有界记录 ──────────────────────────────────


def _record(ctx, key, rec, cap):
    lst = ctx.storage.get(key)
    if not isinstance(lst, list):
        lst = []
    lst.append(rec)
    if len(lst) > cap:
        lst = lst[-cap:]
    ctx.storage[key] = lst


# ── 策略生命周期 ───────────────────────────────────────────────


def on_init(ctx):
    ctx.declare(
        symbol="EURUSD",
        period="H1",
        params={
            # G1 趋势门槛
            "ema_period": 200,
            "long_slope_thr": 0.0001,
            "short_slope_thr": 0.00008,
            # BOLL / 带宽
            "boll_period": 20,
            "boll_dev": 2.0,
            "bw_lookback_long": 20,
            "bw_lookback_short": 15,
            # MACD
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "zero_atr_mult": 0.1,
            # 评分
            "min_score": 70,
            "shadow_min": 60,
            "min_position_score": 20,
            # 风险与手数
            "risk_pct_long": 1.0,
            "risk_pct_short": 0.8,
            "sl_atr_long": 1.0,
            "sl_atr_short": 1.2,
            "lot_mode": "risk",        # risk | fixed
            "fixed_volume": 0.1,
            "value_per_unit": 100000,  # 每 1.0 价格变动每手的价值(EURUSD≈100000)
            "min_lot": 0.01,
            "max_lot": 5.0,
            # P1: 波动率自适应仓位(ATR 百分位)
            "atr_pct_window": 100,
            "atr_pct_high": 80.0,      # 百分位 > 80% -> 降仓
            "atr_pct_low": 30.0,       # 百分位 < 30% -> 微增
            "vol_factor_high": 0.70,
            "vol_factor_low": 1.10,
            # P2: 相关性过滤(逗号分隔的同向相关品种, 如 "GBPUSD,AUDUSD")
            "corr_symbols": "",
            # P2: 资金曲线斜率
            "slope_window": 30,        # 回归样本数(每根K线记一次净值)
            "slope_persist_bars": 120, # 斜率持续为负的K线数阈值(降仓)
            "slope_review_bars": 240,  # 更久 -> 人工复查标记
            # 出场
            "first_tp_long": 50,       # 首批止盈比例%
            "first_tp_short": 60,
            "time_exit_long": 8,       # 时间止损(K线根数)
            "time_exit_short": 6,
            # 熔断
            "max_consec": 3,
            "cooldown_hours": 4,
            "max_consec_hard": 5,
            "cooldown_hard_hours": 24,
            "daily_max_loss_pct": 3.0,
            "weekly_max_loss_pct": 6.0,
            "max_drawdown_pct": 15.0,
            # 数据层
            "record_cap": 500,         # 特征/影子记录容量(条)
            # 历史预热
            "history": 400,
        },
    )


def on_bar(ctx, bar):
    p = ctx.params
    t = bar.time  # UTC

    bars = ctx.get_bars(ctx.symbol, ctx.period, count=p["history"])
    need = max(p["ema_period"], p["boll_period"] + p["bw_lookback_long"],
               p["macd_slow"] + p["macd_signal"], p["atr_pct_window"], 20) + 3
    if len(bars) < need:
        return

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    c1 = closes[-1]

    ema_line = _ema(closes, p["ema_period"])
    up, mid, lo = _boll(closes, p["boll_period"], p["boll_dev"])
    m_main, m_sig, m_hist = _macd(closes, p["macd_fast"], p["macd_slow"], p["macd_signal"])
    atr_line = _atr(highs, lows, closes, 14)

    # 相对带宽 = (upper-middle)/middle：剔除 NaN 头部后构造，
    # 避免把 NaN 传入滚动均值(NaN 会沿滚动和永久传播)
    bw = []
    for i in range(len(closes)):
        if up[i] == up[i] and mid[i] == mid[i] and mid[i] != 0:
            bw.append((up[i] - mid[i]) / mid[i])
    if len(bw) < max(p["bw_lookback_long"], p["bw_lookback_short"]) + 1:
        return
    bw1 = bw[-1]

    # NaN 守卫: 关键序列末四根必须全部有效(动量交叉窗口需要回看 4 根)
    for seq in (ema_line, up, mid, lo, m_main, m_sig, m_hist, atr_line):
        for v in seq[-4:]:
            if v != v:
                return
    a1 = atr_line[-1]

    acct = ctx.get_account()

    # ── 熔断状态(持久化) ──
    fuse = ctx.storage.get("fuse")
    if fuse is None:
        fuse = {
            "consec": 0, "halt_until": None, "cautious": False, "dead": False,
            "peak": acct.equity,
            "day": t.date().isoformat(), "day_eq": acct.equity,
            "week": "{}-W{}".format(*t.isocalendar()), "week_eq": acct.equity,
            "eq_hist": [], "slope_bad": 0, "slope_reviewed": False,
        }
    fuse["peak"] = max(fuse["peak"], acct.equity)
    if t.date().isoformat() != fuse["day"]:
        fuse["day"] = t.date().isoformat()
        fuse["day_eq"] = acct.equity
    wk = "{}-W{}".format(*t.isocalendar())
    if wk != fuse["week"]:
        fuse["week"] = wk
        fuse["week_eq"] = acct.equity

    # P2: 资金曲线斜率(每根K线记录一次净值, 有界)
    eq_hist = fuse.get("eq_hist") or []
    eq_hist.append(acct.equity)
    if len(eq_hist) > p["slope_review_bars"] * 2:
        eq_hist = eq_hist[-p["slope_review_bars"] * 2:]
    fuse["eq_hist"] = eq_hist
    k = min(p["slope_window"], len(eq_hist))
    if k >= 10:
        seg = eq_hist[-k:]
        x_mean = (k - 1) / 2.0
        y_mean = sum(seg) / k
        num = sum((i - x_mean) * (seg[i] - y_mean) for i in range(k))
        den = sum((i - x_mean) ** 2 for i in range(k))
        slope = num / den if den else 0.0
        if slope < 0:
            fuse["slope_bad"] = fuse.get("slope_bad", 0) + 1
        else:
            fuse["slope_bad"] = 0
        if (fuse["slope_bad"] >= p["slope_review_bars"]
                and not fuse.get("slope_reviewed")):
            fuse["slope_reviewed"] = True
            ctx.log.warn("资金曲线斜率持续为负超过阈值, 建议人工复查策略")
    slope_factor = 0.5 if fuse.get("slope_bad", 0) >= p["slope_persist_bars"] else 1.0

    positions = ctx.get_positions()

    # ── 总回撤 15%: 平掉全部跟踪持仓并停机(等待人工) ──
    if not fuse["dead"] and acct.equity < fuse["peak"] * (1 - p["max_drawdown_pct"] / 100.0):
        fuse["dead"] = True
        ctx.log.error(
            "总回撤超过 {:.0f}%，全部平仓并停机(需人工重启)".format(p["max_drawdown_pct"])
        )
        for pos in positions:
            ctx.close(pos.id, comment="v4:MAX_DD")
        ctx.storage["fuse"] = fuse
        return
    if fuse["dead"]:
        ctx.storage["fuse"] = fuse
        return

    # ── 持仓阶段状态(持久化) ──
    stages = ctx.storage.get("stages", {})
    pending = ctx.storage.get("pending")

    # pending -> 挂接到新出现的持仓
    if pending:
        known = set(stages)
        for pos in positions:
            if pos.id not in known:
                stages[pos.id] = {
                    "dir": pending["dir"],
                    "sl": pending["sl"],
                    "entry_time": pending["entry_time"],
                    "score": pending.get("score", 0),
                    "first_done": False,
                    "trail": None,
                    "last_pnl": 0.0,
                }
        ctx.storage["pending"] = None

    # ── 出场管理(熔断中仍执行；仅管理本策略跟踪的持仓) ──
    pos_ids = {pos.id for pos in positions}
    bar_delta = timedelta(seconds=_PERIOD_SECONDS.get(ctx.period, 3600))
    for pid in list(stages):
        st = stages[pid]
        if pid not in pos_ids:
            # 持仓已消失(任意原因平仓) -> 更新连亏 + 记录特征(P1 数据层)
            if st["last_pnl"] < 0:
                fuse["consec"] += 1
            elif st["last_pnl"] > 0:
                fuse["consec"] = 0
            stages.pop(pid)
            ctx.log.info(
                "持仓 {} 已离场, last_pnl={:.2f}, 连亏={}".format(
                    pid, st["last_pnl"], fuse["consec"]
                )
            )
            _record(ctx, "features", {
                "exit_time": _iso(t), "dir": st["dir"], "pnl": round(st["last_pnl"], 2),
                "entry_score": st.get("score", 0),
                "exit_stage": "tp1_done" if st.get("first_done") else "initial",
            }, p["record_cap"])
            if fuse["consec"] >= p["max_consec_hard"]:
                fuse["halt_until"] = _iso(t + timedelta(hours=p["cooldown_hard_hours"]))
                fuse["cautious"] = True
                ctx.log.warn("连亏 {} 笔, 熔断 24 小时, 恢复后首笔减半".format(fuse["consec"]))
            elif fuse["consec"] >= p["max_consec"]:
                fuse["halt_until"] = _iso(t + timedelta(hours=p["cooldown_hours"]))
                fuse["cautious"] = True
                ctx.log.warn("连亏 {} 笔, 熔断 4 小时, 恢复后首笔减半".format(fuse["consec"]))
            continue

        pos = next(x for x in positions if x.id == pid)
        st["last_pnl"] = pos.unrealized_pnl + pos.swap + pos.commission
        is_long = st["dir"] == "buy"

        # 1) 结构止损(收盘级 L0)
        if (is_long and c1 <= st["sl"]) or (not is_long and c1 >= st["sl"]):
            r = ctx.close(pos.id, comment="v4:SL")
            if r.success:
                ctx.log.info("止损离场 {} @ {:.5f}".format(pid, c1))
            continue

        # 2) 时间止损(非对称)
        bars_held = (t - _from_iso(st["entry_time"])) / bar_delta
        limit = p["time_exit_long"] if is_long else p["time_exit_short"]
        if bars_held >= limit:
            r = ctx.close(pos.id, comment="v4:TIMEOUT")
            if r.success:
                ctx.log.info("时间止损(TIMEOUT_EXIT) {} 持有 {:.0f} 根".format(pid, bars_held))
            continue

        # 3) 首批止盈 @ 中轨(非对称比例)
        if not st["first_done"]:
            hit = (is_long and c1 >= mid[-1]) or (not is_long and c1 <= mid[-1])
            if hit:
                pct = p["first_tp_long"] if is_long else p["first_tp_short"]
                part = round(pos.volume * pct / 100.0 + 1e-9, 2)
                if part < p["min_lot"] or (pos.volume - part) < p["min_lot"]:
                    r = ctx.close(pos.id, comment="v4:TP_FULL")
                    if r.success:
                        ctx.log.info("仓位过小, 中轨全平 {}".format(pid))
                    continue
                r = ctx.close(pos.id, volume=part, comment="v4:TP1")
                if r.success:
                    st["first_done"] = True
                    st["trail"] = mid[-1]  # 阶段1: 跟踪线=中轨(保本线)
                    ctx.log.info(
                        "首批止盈 {}% @ 中轨 {:.5f}, 剩余启动跟踪".format(pct, mid[-1])
                    )
            continue

        # 4) 四阶段动态跟踪线(首批平仓后)
        trail = st["trail"]
        if is_long:
            if c1 > up[-1] * 0.8:
                trail = max(trail, up[-1] * 0.95)
            if c1 >= up[-1]:
                trail = max(trail, up[-1] - 0.5 * a1)
            if c1 <= trail:
                r = ctx.close(pos.id, comment="v4:TRAIL")
                if r.success:
                    ctx.log.info("跟踪线离场 {} @ {:.5f}".format(pid, c1))
                continue
        else:
            if c1 < lo[-1] * 1.2:
                trail = min(trail, lo[-1] * 1.05)
            if c1 <= lo[-1]:
                trail = min(trail, lo[-1] + 0.5 * a1)
            if c1 >= trail:
                r = ctx.close(pos.id, comment="v4:TRAIL")
                if r.success:
                    ctx.log.info("跟踪线离场 {} @ {:.5f}".format(pid, c1))
                continue
        st["trail"] = trail

    # ── 熔断检查(只挡新开仓, 不挡上面的出场管理) ──
    halted = fuse["halt_until"] is not None and t < _from_iso(fuse["halt_until"])
    if fuse["halt_until"] is not None and not halted:
        fuse["halt_until"] = None  # 熔断到期, 保持 cautious 直至首笔开仓

    if halted:
        ctx.storage["fuse"] = fuse
        ctx.storage["stages"] = stages
        return

    # 日亏/周亏(收盘级): 到期自动恢复
    day_halt = acct.equity <= fuse["day_eq"] * (1 - p["daily_max_loss_pct"] / 100.0)
    week_halt = acct.equity <= fuse["week_eq"] * (1 - p["weekly_max_loss_pct"] / 100.0)
    if day_halt or week_halt:
        ctx.log.warn("日亏/周亏熔断触发, 当日/本周停止新开仓")
        ctx.storage["fuse"] = fuse
        ctx.storage["stages"] = stages
        return

    # ── 入场评估 ──
    has_tracked = bool(stages) or ctx.storage.get("pending")
    same_symbol = any(pos.symbol == ctx.symbol for pos in positions)
    session = _session_state(t)
    if has_tracked or same_symbol or session == "block":
        ctx.storage["fuse"] = fuse
        ctx.storage["stages"] = stages
        return

    # G1 趋势门槛
    slope = ema_line[-1] - ema_line[-2]
    gate_long = c1 > ema_line[-1] and slope > p["long_slope_thr"]
    gate_short = c1 < ema_line[-1] and slope < -p["short_slope_thr"]
    if not (gate_long or gate_short):
        ctx.storage["fuse"] = fuse
        ctx.storage["stages"] = stages
        return

    is_long = gate_long
    side = "buy" if is_long else "sell"

    # P2: 相关性过滤 —— 相关品种存在同向持仓时拒绝新开仓
    corr_raw = str(p.get("corr_symbols", "") or "")
    corr_list = [s.strip().upper() for s in corr_raw.split(",") if s.strip()]
    if corr_list and any(
        pos.symbol.upper() in corr_list and pos.direction == side
        for pos in positions
    ):
        ctx.log.info("相关性过滤: 相关品种存在同向持仓, 拒绝新开仓")
        ctx.storage["fuse"] = fuse
        ctx.storage["stages"] = stages
        return

    # S1 位置分(同 bar 对齐): A=下/上轨收回, B=站上/下中轨
    if is_long:
        trig_a = lows[-2] <= lo[-2] and c1 > lo[-1]
        trig_b = c1 > mid[-1] and mid[-1] >= mid[-2]
        pos_score = 0
        if trig_a:
            pos_score = 20
            if mid[-1] >= mid[-2]:
                pos_score += 5
            if trig_b:
                pos_score += 5
        elif trig_b:
            pos_score = 18
    else:
        trig_a = highs[-2] >= up[-2] and c1 < up[-1]
        trig_b = c1 < mid[-1] and mid[-1] <= mid[-2]
        pos_score = 0
        if trig_a:
            pos_score = 20
            if mid[-1] <= mid[-2]:
                pos_score += 5
            if trig_b:
                pos_score += 5
        elif trig_b:
            pos_score = 18

    # S2 动量分: 交叉 15 + 零轴加成 + 柱状线加成
    # 金叉/死叉判定放宽为最近 3 根内出现(与位置触发不必同 bar, 否则联合概率过低;
    # v2.0 原案亦允许"柱状线由负转正"替代交叉), 零轴与柱状线加成仍取 [-1] 当前值
    cross = False
    for k in range(1, 4):
        if is_long:
            crossed = m_main[-k - 1] <= m_sig[-k - 1] and m_main[-k] > m_sig[-k]
        else:
            crossed = m_main[-k - 1] >= m_sig[-k - 1] and m_main[-k] < m_sig[-k]
        if crossed:
            cross = True
            break
    mom_score = 0
    if cross:
        mom_score = 15
        if (is_long and m_main[-1] > 0) or (not is_long and m_main[-1] < 0):
            mom_score += 10   # 顺势
        elif (is_long and m_main[-1] < 0) or (not is_long and m_main[-1] > 0):
            mom_score -= 5    # 逆势
        if abs(m_main[-1]) < a1 * p["zero_atr_mult"]:
            mom_score -= 10   # 震荡区间降权
    if m_hist[-1] > m_hist[-2] > m_hist[-3]:
        mom_score += 10       # 柱状线连续放大
    elif m_hist[-1] < m_hist[-2] < m_hist[-3]:
        mom_score -= 5        # 动量衰减

    # S3 波动分: 带宽 > SMA(带宽) (非对称回看)
    lookback = p["bw_lookback_long"] if is_long else p["bw_lookback_short"]
    bw_sma = _sma(bw, lookback)
    if bw_sma[-1] != bw_sma[-1]:  # NaN(历史不足)
        ctx.storage["fuse"] = fuse
        ctx.storage["stages"] = stages
        return
    vol_score = 15 if bw1 > bw_sma[-1] else 0

    # S4 形态分(P2): 信号K线连续强度
    pat_score = _pattern_score(bars[-3:], is_long)

    # S5 背离分(P2): 顶底背离
    div_score = _divergence_score(lows, highs, m_main, is_long)

    total = pos_score + mom_score + vol_score + pat_score + div_score

    if total >= p["min_score"] and pos_score >= p["min_position_score"]:
        # 止损价
        if is_long:
            sl_price = lo[-1] - p["sl_atr_long"] * a1
            risk_pct = p["risk_pct_long"]
            tag = "LONG"
        else:
            sl_price = up[-1] + p["sl_atr_short"] * a1
            risk_pct = p["risk_pct_short"]
            tag = "SHORT"

        # 手数 = 风险基础 x 波动率系数 x 时段系数 x 斜率系数 x 谨慎系数
        # P1: ATR 百分位系数
        atr_seg = [v for v in atr_line if v == v][-p["atr_pct_window"]:]
        if atr_seg:
            atr_pct = sum(1 for v in atr_seg if v <= a1) / len(atr_seg)
            if atr_pct > p["atr_pct_high"] / 100.0:
                atr_factor = p["vol_factor_high"]
            elif atr_pct < p["atr_pct_low"] / 100.0:
                atr_factor = p["vol_factor_low"]
            else:
                atr_factor = 1.0
        else:
            atr_pct, atr_factor = 0.5, 1.0
        session_factor = 0.5 if session == "caution" else 1.0
        cautious_factor = 0.5 if fuse["cautious"] else 1.0

        if p["lot_mode"] == "fixed":
            lot = round(p["fixed_volume"], 2)
        else:
            sl_dist = abs(c1 - sl_price)
            if sl_dist <= 0:
                ctx.storage["fuse"] = fuse
                ctx.storage["stages"] = stages
                return
            risk_amt = acct.equity * risk_pct / 100.0
            lot = risk_amt / (sl_dist * p["value_per_unit"])
            lot *= atr_factor * session_factor * slope_factor * cautious_factor
            lot = max(p["min_lot"], min(p["max_lot"], round(lot, 2)))

        order = (ctx.buy if is_long else ctx.sell)(
            ctx.symbol, lot, comment="v4:{} score={}".format(tag, total)
        )
        if order.success:
            ctx.log.info(
                "{} 开仓 score={} (位置{} 动量{} 波动{} 形态{} 背离{}) lot={} "
                "atr_pct={:.0%} 系数[波动{} 时段{} 斜率{} 谨慎{}] sl={:.5f}".format(
                    tag, total, pos_score, mom_score, vol_score, pat_score, div_score,
                    lot, atr_pct, atr_factor, session_factor, slope_factor,
                    cautious_factor, sl_price,
                )
            )
            fuse["cautious"] = False
            ctx.storage["pending"] = {
                "dir": side, "sl": sl_price, "entry_time": _iso(t), "score": total,
            }
        else:
            ctx.log.warn("开仓失败: {} - {}".format(order.error_code, order.error_message))
    elif total >= p["shadow_min"]:
        # 影子信号(P1 数据层): 供 AI 校准阈值与挖掘被埋没模式
        ctx.log.info(
            "[SHADOW] {} score={} (位置{} 动量{} 波动{} 形态{} 背离{}) 未达阈值 {}".format(
                "LONG" if is_long else "SHORT", total,
                pos_score, mom_score, vol_score, pat_score, div_score, p["min_score"],
            )
        )
        _record(ctx, "shadow", {
            "t": _iso(t), "dir": side, "score": total,
            "pos": pos_score, "mom": mom_score, "vol": vol_score,
            "pat": pat_score, "div": div_score,
        }, p["record_cap"])

    ctx.storage["fuse"] = fuse
    ctx.storage["stages"] = stages


def on_order(ctx, order):
    if order.success:
        ctx.log.info(
            "订单成交: {} volume={} price={}".format(
                order.order_id, order.filled_volume, order.filled_price
            )
        )


def on_stop(ctx, reason):
    ctx.log.info("策略停止: {}".format(reason))
