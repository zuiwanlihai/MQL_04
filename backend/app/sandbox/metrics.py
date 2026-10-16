"""Pure report metrics for backtest results (M4).

All functions are deterministic pure math over per-bar equity series and
closed-trade lists — no I/O, no clock.  ``bars_per_year`` uses calendar
seconds (31536000 / tf_seconds), matching the PRD metric definitions.

Return series convention: per-bar simple returns of the equity curve.
Sharpe/Sortino are annualised with sqrt(bars_per_year), risk-free = 0.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from datetime import UTC, datetime

from app.sandbox.matching import TF_SECONDS

_SECONDS_PER_YEAR = 31_536_000


def bars_per_year(timeframe: str) -> float:
    return _SECONDS_PER_YEAR / TF_SECONDS[timeframe]


def per_bar_returns(equity: list[float]) -> list[float]:
    out: list[float] = []
    for prev, cur in zip(equity, equity[1:]):
        out.append((cur - prev) / prev if prev > 0 else 0.0)
    return out


def max_drawdown(equity: list[float]) -> tuple[float, float]:
    """(max drawdown amount, max drawdown percent) over the series."""
    peak = -math.inf
    dd_amount = 0.0
    dd_percent = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            drop = peak - v
            pct = drop / peak
            if drop > dd_amount:
                dd_amount = drop
            if pct > dd_percent:
                dd_percent = pct
    return dd_amount, dd_percent


def annual_return_percent(equity: list[float], tf: str) -> float:
    if len(equity) < 2 or equity[0] <= 0 or equity[-1] <= 0:
        return 0.0
    n = len(equity) - 1
    bpy = bars_per_year(tf)
    if n <= 0:
        return 0.0
    growth = equity[-1] / equity[0]
    if growth <= 0:
        return -100.0
    return (growth ** (bpy / n) - 1.0) * 100.0


def annual_volatility_percent(equity: list[float], tf: str) -> float:
    rets = per_bar_returns(equity)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(bars_per_year(tf)) * 100.0


def sharpe_ratio(equity: list[float], tf: str) -> float:
    rets = per_bar_returns(equity)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return mean / std * math.sqrt(bars_per_year(tf))


def sortino_ratio(equity: list[float], tf: str) -> float:
    rets = per_bar_returns(equity)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    downside = [r for r in rets if r < 0]
    if not downside:
        return 0.0
    dvar = sum(r**2 for r in downside) / len(rets)
    dstd = math.sqrt(dvar)
    if dstd == 0:
        return 0.0
    return mean / dstd * math.sqrt(bars_per_year(tf))


def calmar_ratio(equity: list[float], tf: str) -> float:
    ann = annual_return_percent(equity, tf) / 100.0
    _, dd_pct = max_drawdown(equity)
    if dd_pct == 0:
        return 0.0
    return ann / dd_pct


def recovery_factor(equity: list[float], trades: list[dict]) -> float:
    net = sum(t["profit"] for t in trades)
    dd_amount, _ = max_drawdown(equity)
    if dd_amount == 0:
        return 0.0
    return net / dd_amount


def trade_stats(trades: list[dict]) -> dict:
    closed = [t for t in trades if t.get("close_time") is not None]
    wins = [t for t in closed if t["profit"] > 0]
    losses = [t for t in closed if t["profit"] <= 0]
    gross_win = sum(t["profit"] for t in wins)
    gross_loss = -sum(t["profit"] for t in losses)
    n = len(closed)
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    win_rate = len(wins) / n if n else 0.0
    expectancy = (sum(t["profit"] for t in closed) / n) if n else 0.0
    # JSON-safe: no all-losing/all-winning edge case may emit Infinity.
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (
        9999.0 if gross_win > 0 else 0.0
    )
    cw = cl = mcw = mcl = 0
    for t in closed:
        if t["profit"] > 0:
            cw += 1
            cl = 0
        else:
            cl += 1
            cw = 0
        mcw = max(mcw, cw)
        mcl = max(mcl, cl)
    holds = [
        (t["close_time"] - t["open_time"]) / 3600.0
        for t in closed
        if t.get("open_time") is not None and t.get("close_time") is not None
    ]
    avg_hold_hours = sum(holds) / len(holds) if holds else 0.0
    return {
        "total_trades": n,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "profit_factor": profit_factor,
        "consecutive_wins": mcw,
        "consecutive_losses": mcl,
        "avg_hold_hours": avg_hold_hours,
    }


def monthly_returns(equity_times: list[int], equity: list[float]) -> "OrderedDict[str, float]":
    """Percent change of equity per UTC calendar month (first/last point)."""
    if not equity_times:
        return OrderedDict()
    months: "OrderedDict[str, list[float]]" = OrderedDict()
    for t, v in zip(equity_times, equity):
        key = datetime.fromtimestamp(t, tz=UTC).strftime("%Y-%m")
        months.setdefault(key, []).append(v)
    out: "OrderedDict[str, float]" = OrderedDict()
    prev_end = equity[0]
    for key, values in months.items():
        end = values[-1]
        out[key] = (end - prev_end) / prev_end * 100.0 if prev_end > 0 else 0.0
        prev_end = end
    return out


def buy_hold_baseline(
    times: list[int], closes: list[float], initial_deposit: float
) -> list[float]:
    """Buy-and-hold reference curve: deposit × (close_i / close_0)."""
    if not closes or closes[0] == 0:
        return [initial_deposit] * len(times)
    factor = initial_deposit / closes[0]
    return [c * factor for c in closes]


def downsample_indices(n: int, max_points: int) -> list[int]:
    """Uniformly spaced indices covering [0, n-1], always including both
    ends, at most ``max_points`` entries."""
    if n <= max_points:
        return list(range(n))
    step = (n - 1) / (max_points - 1)
    idx = [int(round(k * step)) for k in range(max_points)]
    # de-duplicate while preserving order
    seen: dict[int, None] = {}
    for i in idx:
        seen[i] = None
    return list(seen.keys())


# ======================================================================
# Strategy Scoring (0-100, 多维度加权 + 可信度系数)
# ======================================================================

def _seg(x: float, anchors: list[tuple[float, float]]) -> float:
    """分段线性映射。anchors 形如 [(x0, y0), (x1, y1), ...]，x 升序、y 在 [0,100]。"""
    if x <= anchors[0][0]:
        return anchors[0][1]
    for i in range(1, len(anchors)):
        x0, y0 = anchors[i - 1]
        x1, y1 = anchors[i]
        if x <= x1:
            t = (x - x0) / (x1 - x0) if x1 != x0 else 0.0
            return y0 + (y1 - y0) * t
    return anchors[-1][1]


def _seg_rev(x: float, anchors: list[tuple[float, float]]) -> float:
    """越低越好版的分段线性（anchors 还是 x 升序）。"""
    return _seg(-x, [(-a[0], a[1]) for a in reversed(anchors)])


def strategy_score(report: dict) -> dict:
    """对回测报告打综合分 0-100，含维度分、等级、可信度、亮点短板。

    四个维度各 0-100，加权后乘可信度系数。
    """

    # ── 指标提取 ──
    ann_ret = float(report.get("annual_return_percent") or 0)
    tot_ret = float(report.get("total_return_percent") or 0)
    sharpe = float(report.get("sharpe_ratio") or 0)
    sortino = float(report.get("sortino_ratio") or 0)
    calmar = float(report.get("calmar_ratio") or 0)
    maxdd = float(report.get("max_drawdown_percent") or 0)
    recov = float(report.get("recovery_factor") or 0)
    winrate = float(report.get("win_rate") or 0)
    pf = float(report.get("profit_factor") or 0)
    expect = float(report.get("expectancy") or 0)
    trades = int(report.get("total_trades") or 0)

    if trades == 0:
        return {
            "score": 0,
            "grade": "F",
            "grade_label": "无交易",
            "credibility": 0.0,
            "credibility_note": "0 笔成交，无法评估策略质量",
            "dimensions": {},
            "highlights": [],
            "shortcomings": ["本区间未触发任何开仓信号，策略完全未被验证"],
        }

    # ── 归一化每个指标到 [0,100] ──
    n_ann = _seg(ann_ret, [(-20, 0), (0, 30), (10, 60), (30, 80), (50, 100)])
    n_tot = _seg(tot_ret, [(-30, 0), (0, 20), (50, 60), (100, 80), (200, 100)])
    n_sharpe = _seg(sharpe, [(-0.5, 0), (0, 20), (1, 60), (2, 85), (3, 100)])
    n_sortino = _seg(sortino, [(-0.5, 0), (0, 20), (1, 55), (2, 80), (3, 100)])
    n_calmar = _seg(calmar, [(0, 0), (0.5, 30), (1, 55), (2, 80), (3, 100)])
    n_maxdd = _seg_rev(maxdd, [(5, 100), (10, 80), (20, 55), (30, 30), (50, 0)])
    n_recov = _seg(recov, [(0, 10), (0.5, 40), (1, 60), (2, 85), (3, 100)])
    # 胜率过高可能过拟合，60% 左右最佳
    if winrate <= 0.40:
        n_wr = _seg(winrate, [(0, 0), (0.30, 10), (0.40, 30)])
    elif winrate <= 0.60:
        n_wr = _seg(winrate, [(0.40, 30), (0.50, 60), (0.55, 80), (0.60, 100)])
    else:
        n_wr = _seg(winrate, [(0.60, 100), (0.70, 80), (0.85, 50)])
    n_pf = _seg(pf, [(0, 0), (1, 30), (1.5, 55), (2, 80), (3, 100)])
    n_ex = _seg(expect, [(-5, 0), (0, 20), (1, 50), (5, 80), (10, 100)])
    n_tr = _seg(float(trades), [(5, 0), (15, 25), (30, 50), (50, 75), (100, 90), (200, 100)])

    # ── 四个维度 ──
    dim_return = round(n_ann * 0.7 + n_tot * 0.3, 1)  # 30%
    dim_risk_adj = round(n_sharpe * 0.4 + n_sortino * 0.3 + n_calmar * 0.3, 1)  # 30%
    dim_drawdown = round(n_maxdd * 0.6 + n_recov * 0.4, 1)  # 20%
    dim_quality = round(n_wr * 0.25 + n_pf * 0.35 + n_ex * 0.20 + n_tr * 0.20, 1)  # 20%

    raw = dim_return * 0.30 + dim_risk_adj * 0.30 + dim_drawdown * 0.20 + dim_quality * 0.20

    # ── 可信度系数：交易太少不可靠 ──
    if trades < 10:
        credibility = 0.5
        cred_note = f"仅 {trades} 笔成交，样本太少，评分参考价值有限"
    elif trades < 30:
        credibility = 0.75
        cred_note = f"{trades} 笔成交，样本偏小，建议扩大回测区间"
    elif trades < 50:
        credibility = 0.9
        cred_note = f"{trades} 笔成交，样本基本够用"
    else:
        credibility = 1.0
        cred_note = f"{trades} 笔成交，样本充足"

    final = round(raw * credibility, 1)
    final = max(0.0, min(100.0, final))

    # ── 等级 ──
    if final >= 85:
        grade, glabel = "S", "顶级"
    elif final >= 70:
        grade, glabel = "A", "优秀"
    elif final >= 55:
        grade, glabel = "B", "良好"
    elif final >= 40:
        grade, glabel = "C", "一般"
    elif final >= 25:
        grade, glabel = "D", "较差"
    else:
        grade, glabel = "F", "失败"

    # ── 亮点 & 短板（自动识别 top2 好 / top2 差）──
    metric_labels = [
        ("年化收益", n_ann, f"{ann_ret:+.1f}%"),
        ("总收益", n_tot, f"{tot_ret:+.1f}%"),
        ("夏普比率", n_sharpe, f"{sharpe:.2f}"),
        ("索提诺", n_sortino, f"{sortino:.2f}"),
        ("卡玛比率", n_calmar, f"{calmar:.2f}"),
        ("最大回撤", n_maxdd, f"{maxdd:.1f}%"),
        ("恢复因子", n_recov, f"{recov:.2f}"),
        ("胜率", n_wr, f"{winrate * 100:.1f}%"),
        ("盈亏比", n_pf, f"{pf:.2f}"),
        ("期望收益", n_ex, f"{expect:+.2f}"),
        ("样本量", n_tr, f"{trades} 笔"),
    ]
    sorted_metrics = sorted(metric_labels, key=lambda x: x[1])
    shortcomings = [f"{label} {val}（{raw}）" for label, raw, val in sorted_metrics[:2] if raw < 50]
    highlights = [f"{label} {val}" for label, raw, val in reversed(sorted_metrics[-2:]) if raw >= 70]

    if ann_ret < -10:
        shortcomings.append("年化收益大幅为负，策略可能方向性错误")
    if maxdd > 40:
        shortcomings.append(f"最大回撤 {maxdd:.0f}% 超过 40%，风险极高")
    if sharpe < 0:
        shortcomings.append("夏普比率为负，风险调整后无正向价值")

    return {
        "score": final,
        "grade": grade,
        "grade_label": glabel,
        "credibility": credibility,
        "credibility_note": cred_note,
        "dimensions": {
            "收益能力": dim_return,
            "风险调整": dim_risk_adj,
            "回撤控制": dim_drawdown,
            "交易质量": dim_quality,
        },
        "highlights": highlights,
        "shortcomings": shortcomings,
    }
