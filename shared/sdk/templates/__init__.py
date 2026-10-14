"""
Built-in strategy templates (SDK v1 starter library).

Each entry maps the template file stem to (display name, category,
description, default parameters).  The alembic seed migration reads the
``.py`` sources from this directory and inserts them into the
``strategy_templates`` table.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent

# stem -> (name, category, description, parameters)
TEMPLATE_META: dict[str, tuple[str, str, str, dict[str, str]]] = {
    "double_ma": (
        "双均线交叉",
        "trend",
        "经典趋势跟踪：快线上穿慢线做多，下穿平仓。同一时间仅持有一单。",
        {"fast_period": "5", "slow_period": "20", "volume": "0.1"},
    ),
    "grid_trading": (
        "网格交易",
        "grid",
        "锚定价下方按固定间距挂买、上涨一格平仓；演示 ctx.storage 跨重启保存网格状态。",
        {"grid_step": "0.0020", "max_levels": "5", "volume": "0.1"},
    ),
    "channel_breakout": (
        "唐奇安通道突破",
        "trend",
        "突破前 N 根最高价进场，跌破前 M 根最低价离场的纯 Python 通道突破。",
        {"entry_window": "20", "exit_window": "10", "volume": "0.1"},
    ),
    "rsi_mean_reversion": (
        "RSI 均值回归",
        "mean_reversion",
        "RSI 低于超卖阈值买入、高于超买阈值平仓；内置 Wilder RSI 纯 Python 实现。",
        {"rsi_period": "14", "oversold": "30", "overbought": "70", "volume": "0.1"},
    ),
    "atr_stop_management": (
        "ATR 移动止损",
        "risk_management",
        "均线金叉进场 + ATR 移动止损离场；明示 MVP 无券商保护单，止损由策略自行执行。",
        {"fast_period": "10", "slow_period": "30", "atr_period": "14",
         "atr_mult": "2.0", "volume": "0.1"},
    ),
    "boll_macd_ma_v4": (
        "BOLL+MACD+MA 融合 v4.0",
        "trend",
        "v4.0 方案完整版（MVP+P1+P2）：门槛-评分双层结构，EMA200 趋势门槛"
        " + 位置/动量/波动/形态/背离五维评分（总分≥70 开仓，60~69 记影子信号）；"
        "非对称多空（斜率/止损/止盈/时间止损）；五系数手数（风险基础×ATR百分位×时段"
        "×资金曲线斜率×熔断谨慎）；DST 自适应时段过滤（重叠窗口最佳、纽约尾盘降仓）；"
        "中轨分批止盈 + 四阶段跟踪线 + 时间止损；连亏/日亏/周亏/总回撤四级熔断；"
        "相关性品种同向敞口过滤；平仓特征与影子信号有界记录供 AI 校准。"
        "状态经 ctx.storage 持久化跨重启恢复。止损为收盘级执行。",
        {
            "ema_period": "200", "long_slope_thr": "0.0001",
            "short_slope_thr": "0.00008", "boll_period": "20", "boll_dev": "2.0",
            "bw_lookback_long": "20", "bw_lookback_short": "15",
            "macd_fast": "12", "macd_slow": "26", "macd_signal": "9",
            "zero_atr_mult": "0.1", "min_score": "70", "shadow_min": "60",
            "min_position_score": "20", "risk_pct_long": "1.0",
            "risk_pct_short": "0.8", "sl_atr_long": "1.0", "sl_atr_short": "1.2",
            "lot_mode": "risk", "fixed_volume": "0.1", "value_per_unit": "100000",
            "min_lot": "0.01", "max_lot": "5.0",
            "atr_pct_window": "100", "atr_pct_high": "80.0", "atr_pct_low": "30.0",
            "vol_factor_high": "0.70", "vol_factor_low": "1.10",
            "corr_symbols": "", "slope_window": "30", "slope_persist_bars": "120",
            "slope_review_bars": "240", "record_cap": "500",
            "first_tp_long": "50", "first_tp_short": "60",
            "time_exit_long": "8", "time_exit_short": "6",
            "max_consec": "3", "cooldown_hours": "4", "max_consec_hard": "5",
            "cooldown_hard_hours": "24", "daily_max_loss_pct": "3.0",
            "weekly_max_loss_pct": "6.0", "max_drawdown_pct": "15.0",
            "history": "400",
        },
    ),
}

TEMPLATE_FILES: list[str] = list(TEMPLATE_META)


def template_source(stem: str) -> str:
    """Read the template source code from disk."""
    return (TEMPLATE_DIR / f"{stem}.py").read_text(encoding="utf-8")


def seed_rows() -> list[dict]:
    """Template rows ready for ``INSERT INTO strategy_templates``.

    Used by the alembic seed migration and the test conftest alike.
    ``id`` is a deterministic UUID5 of the stem so re-seeding is idempotent.
    """
    import uuid as _uuid

    ns = _uuid.UUID("6f6c656e-7469-4669-7865-640000000001")
    rows = []
    for order, (stem, (name, category, description, params)) in enumerate(
        TEMPLATE_META.items(), start=1
    ):
        rows.append(
            {
                "id": _uuid.uuid5(ns, stem),
                "name": name,
                "description": description,
                "language": "python",
                "category": category,
                "sort_order": order,
                "parameters": params,
                "source_code": template_source(stem),
            }
        )
    return rows
