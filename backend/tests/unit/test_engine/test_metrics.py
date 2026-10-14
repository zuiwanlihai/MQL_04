"""Pure metric functions — hand-computed expectations."""

from __future__ import annotations

import math

from app.sandbox import metrics


def test_bars_per_year():
    assert metrics.bars_per_year("H1") == 31536000 / 3600
    assert metrics.bars_per_year("D1") == 365.0


def test_per_bar_returns():
    assert metrics.per_bar_returns([100, 110, 99]) == [0.1, -0.1]


def test_max_drawdown():
    amount, pct = metrics.max_drawdown([100, 120, 90, 110])
    assert amount == 30.0
    assert pct == 30.0 / 120.0


def test_max_drawdown_monotonic_up():
    amount, pct = metrics.max_drawdown([100, 110, 120])
    assert amount == 0.0 and pct == 0.0


def test_annual_return_percent_exact():
    # 365 daily bars, growth 1.1 → exactly 10% annualised for D1.
    eq = [1000.0] * 365 + [1100.0]
    assert abs(metrics.annual_return_percent(eq, "D1") - 10.0) < 1e-9


def test_annual_return_short_series():
    assert metrics.annual_return_percent([1000], "D1") == 0.0


def test_sharpe_zero_when_constant_returns():
    eq = [100.0, 110.0, 121.0]  # +10% each bar → std 0
    assert metrics.sharpe_ratio(eq, "H1") == 0.0


def test_sharpe_positive_for_growth():
    eq = [1000.0, 1010.0, 1005.0, 1020.0, 1030.0]
    assert metrics.sharpe_ratio(eq, "H1") > 0.0


def test_sortino_no_downside_returns_zero():
    eq = [1000.0, 1010.0, 1020.0]
    assert metrics.sortino_ratio(eq, "H1") == 0.0


def test_calmar_uses_drawdown():
    eq = [1000.0, 900.0, 1100.0]
    ann = metrics.annual_return_percent(eq, "D1") / 100.0
    _, dd = metrics.max_drawdown(eq)
    assert metrics.calmar_ratio(eq, "D1") == ann / dd


def test_recovery_factor():
    eq = [1000.0, 900.0, 1000.0]
    trades = [{"profit": 100.0}]
    # dd_amount = 100, net = 100 → 1.0
    assert metrics.recovery_factor(eq, trades) == 1.0


def test_trade_stats_hand_computed():
    trades = [
        {"open_time": 3600, "close_time": 7200, "profit": 10.0},
        {"open_time": 7200, "close_time": 10800, "profit": -5.0},
        {"open_time": 10800, "close_time": 14400, "profit": 15.0},
    ]
    s = metrics.trade_stats(trades)
    assert s["total_trades"] == 3
    assert s["winning_trades"] == 2 and s["losing_trades"] == 1
    assert s["win_rate"] == 2 / 3
    assert s["avg_win"] == 12.5 and s["avg_loss"] == 5.0
    assert s["expectancy"] == 20.0 / 3.0
    assert s["profit_factor"] == 5.0
    assert s["consecutive_wins"] == 1 and s["consecutive_losses"] == 1
    holds = [(7200 - 3600) / 3600, (10800 - 7200) / 3600, (14400 - 10800) / 3600]
    assert s["avg_hold_hours"] == sum(holds) / 3


def test_trade_stats_profit_factor_no_loss():
    trades = [{"open_time": 0, "close_time": 3600, "profit": 10.0}]
    s = metrics.trade_stats(trades)
    assert s["profit_factor"] == 9999.0


def test_monthly_returns_two_months():
    t_jan = 1_767_225_600  # 2026-01-01 UTC
    t_jan2 = t_jan + 86400 * 15
    t_feb = 1_769_904_000  # 2026-02-01 UTC
    out = metrics.monthly_returns([t_jan, t_jan2, t_feb], [1000.0, 1100.0, 1200.0])
    keys = list(out)
    assert keys == ["2026-01", "2026-02"]
    assert out["2026-01"] == 10.0
    assert abs(out["2026-02"] - 100.0 / 11.0) < 1e-9


def test_buy_hold_baseline():
    closes = [2.0, 2.2, 1.1]
    out = metrics.buy_hold_baseline([0, 1, 2], closes, 1000.0)
    assert out == [1000.0, 1100.0, 550.0]


def test_downsample_indices_small():
    assert metrics.downsample_indices(5, 10) == [0, 1, 2, 3, 4]


def test_downsample_indices_ends_included():
    idx = metrics.downsample_indices(10, 5)
    assert idx[0] == 0 and idx[-1] == 9
    assert idx == sorted(set(idx))
    assert len(idx) <= 5


def test_annual_volatility_positive():
    eq = [1000.0, 1010.0, 1005.0, 1020.0]
    v = metrics.annual_volatility_percent(eq, "H1")
    assert v > 0.0
    # cross-check: sample std × sqrt(bpy) × 100
    rets = metrics.per_bar_returns(eq)
    mean = sum(rets) / len(rets)
    std = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
    assert v == std * math.sqrt(metrics.bars_per_year("H1")) * 100.0
