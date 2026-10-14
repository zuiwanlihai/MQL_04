"""SDK 内置指标单元测试（P2-7）。

输出契约（模块 docstring 承诺）：与输入等长，数据不足处为 NaN。
各测试里的锚点值全部手工推导并写在断言旁，锁住数值行为——
MACD/KDJ 的种子口径为 MT5/前端镜像口径（P2-7 修复点），改动这里必须同步前端。
"""

from __future__ import annotations

import math

import pytest
from sdk.indicators import adx, atr, boll, cci, ema, kdj, macd, obv, rsi, sma, wma


def assert_nan_prefix(values: list[float], prefix: int) -> None:
    """前 prefix 个为 NaN，其余全部有值，长度不变。"""
    assert len(values) >= prefix
    assert all(math.isnan(v) for v in values[:prefix])
    assert all(not math.isnan(v) for v in values[prefix:])


def ramp(n: int, base: float = 100.0, step: float = 0.5) -> list[float]:
    return [base + i * step for i in range(n)]


# ─── SMA / EMA / WMA ───────────────────────────────────────────

class TestSma:
    def test_ramp_window_midpoint(self):
        data = ramp(10, 100, 2)  # 100,102,...,118
        out = sma(data, 5)
        assert_nan_prefix(out, 4)
        assert out[4] == pytest.approx(104)  # (100+102+104+106+108)/5
        assert out[9] == pytest.approx(114)  # (110+112+114+116+118)/5

    def test_insufficient_data_all_nan(self):
        assert all(math.isnan(v) for v in sma(ramp(3), 5))
        assert sma([], 5) == []

    def test_period_one_is_identity(self):
        data = ramp(4)
        assert sma(data, 1) == data


class TestEma:
    def test_sma_seed_then_recursion(self):
        data = ramp(8, 100, 1)  # 100..107
        out = ema(data, 4)
        assert_nan_prefix(out, 3)
        assert out[3] == pytest.approx(101.5)  # SMA seed
        k = 2 / 5
        assert out[4] == pytest.approx(104 * k + 101.5 * (1 - k))

    def test_flat_series_stays_flat(self):
        out = ema([5.0] * 10, 3)
        assert_nan_prefix(out, 2)
        assert out[2:] == [5.0] * 8

    def test_insufficient_data_all_nan(self):
        assert all(math.isnan(v) for v in ema(ramp(2), 4))


class TestWma:
    def test_hand_computed(self):
        # period=2: [1*1+2*2]/3, [2*1+3*2]/3
        out = wma([1, 2, 3], 2)
        assert_nan_prefix(out, 1)
        assert out[1:] == pytest.approx([5 / 3, 8 / 3])
        # period=3: (1*1+2*2+3*3)/6
        out = wma([1, 2, 3], 3)
        assert_nan_prefix(out, 2)
        assert out[2] == pytest.approx(14 / 6)

    def test_insufficient_data_all_nan(self):
        out = wma(ramp(2), 3)
        assert len(out) == 2
        assert all(math.isnan(v) for v in out)


# ─── MACD ──────────────────────────────────────────────────────

class TestMacd:
    """MT5 口径：signal 在 MACD 线有效段上做 EMA，种子为前 signal 个有效值的 SMA。

    P2-7 修复前：signal 对前导无效区补 0 后整体做 EMA，种子被 0 污染
    （ramp 上 dea[33] = 3.0302，前端/MT5 = 3.5，hist 末值连符号都不同）。
    """

    def test_nan_prefix_invariants(self):
        dif, dea, hist = macd(ramp(60))
        assert_nan_prefix(dif, 25)  # slow-1
        assert_nan_prefix(dea, 33)  # slow+signal-2
        assert_nan_prefix(hist, 33)

    def test_signal_seed_is_sma_of_first_signal_dif_values(self):
        dif, dea, _ = macd(ramp(60))
        seed = sum(dif[25:34]) / 9
        assert dea[33] == pytest.approx(seed)
        # 等差序列上 EMA 差值恒为 step*(slow-fast)/2 = 0.5*14/2 = 3.5
        assert dea[33] == pytest.approx(3.5)

    def test_histogram_equals_dif_minus_dea(self):
        dif, dea, hist = macd(ramp(60))
        for i in range(33, 60):
            assert hist[i] == pytest.approx(dif[i] - dea[i])

    def test_insufficient_for_signal(self):
        dif, dea, hist = macd(ramp(30))
        assert not math.isnan(dif[29])
        # n=30 < slow+signal-1=33 → signal 全 NaN，hist 也随之全 NaN
        assert all(math.isnan(v) for v in dea)
        assert all(math.isnan(v) for v in hist)

    def test_dea_single_point_when_exactly_enough(self):
        # 首个 dea 下标 = slow+signal-2 = 33 → 至少需要 34 个点
        _, dea_short, _ = macd(ramp(33))
        assert all(math.isnan(v) for v in dea_short)
        _, dea, _ = macd(ramp(34))
        assert_nan_prefix(dea, 33)
        assert dea[33] == pytest.approx(3.5)


# ─── RSI ───────────────────────────────────────────────────────

class TestRsi:
    def test_hand_computed_wilder(self):
        # period=2, closes 10,11,10,11,12 → 手推:
        # idx2: rs=(1/2)/(1/2)=1 → 50; idx3: gain .75/loss .25 → 75; idx4: .875/.125 → 87.5
        out = rsi([10, 11, 10, 11, 12], 2)
        assert_nan_prefix(out, 2)
        assert out[2:] == pytest.approx([50.0, 75.0, 87.5])

    def test_monotonic_series(self):
        assert all(v == pytest.approx(100.0) for v in rsi(ramp(30, 100, 1), 14)[14:])
        assert all(v == pytest.approx(0.0) for v in rsi(ramp(30, 100, -1), 14)[14:])

    def test_insufficient_data_all_nan(self):
        assert all(math.isnan(v) for v in rsi(ramp(14), 14))


# ─── KDJ ───────────────────────────────────────────────────────

class TestKdj:
    """MT5 口径：首根（RSV 可算的第一根）即参与平滑，K/D 从 50 起递推。

    P2-7 修复前：首根 K/D 硬置 50、忽略该根 RSV，J 首根是 NaN。
    """

    def _ramp_hlc(self, n: int) -> tuple[list[float], list[float], list[float]]:
        close = ramp(n, 100, 0.5)
        return [c + 0.5 for c in close], [c - 0.5 for c in close], close

    def test_first_bar_smooths_rsv_in(self):
        high, low, close = self._ramp_hlc(60)
        k, d, j = kdj(high, low, close)
        assert_nan_prefix(k, 8)  # period-1
        assert_nan_prefix(d, 8)
        assert_nan_prefix(j, 8)  # J 首根也有值（修复前是 NaN）
        # idx8: RSV=(104-99.5)/(104.5-99.5)*100=90 → K=(90+2*50)/3, D=(K+2*50)/3
        assert k[8] == pytest.approx(190 / 3)
        assert d[8] == pytest.approx(490 / 9)
        assert j[8] == pytest.approx(730 / 9)  # 3K-2D

    def test_j_equals_3k_minus_2d_throughout(self):
        high, low, close = self._ramp_hlc(60)
        k, d, j = kdj(high, low, close)
        for i in range(8, 60):
            assert j[i] == pytest.approx(3 * k[i] - 2 * d[i])

    def test_flat_bars_rsv_fifty(self):
        # high == low → RSV 取 50 → K/D/J 恒为 50
        flat = [100.0] * 12
        k, d, j = kdj(flat, flat, flat)
        assert k[8:] == pytest.approx([50.0] * 4)
        assert d[8:] == pytest.approx([50.0] * 4)
        assert j[8:] == pytest.approx([50.0] * 4)

    def test_insufficient_data_all_nan(self):
        high, low, close = self._ramp_hlc(8)
        for line in kdj(high, low, close):
            assert all(math.isnan(v) for v in line)


# ─── BOLL ──────────────────────────────────────────────────────

class TestBoll:
    def test_population_std(self):
        # [1,2,3,4,5], period=5: mid=3, var=10/5=2
        mid, upper, lower = boll([1, 2, 3, 4, 5], 5, 2.0)
        assert mid[4] == pytest.approx(3.0)
        assert upper[4] == pytest.approx(3.0 + 2.0 * math.sqrt(2.0))
        assert lower[4] == pytest.approx(3.0 - 2.0 * math.sqrt(2.0))

    def test_constant_series_bands_collapse(self):
        mid, upper, lower = boll([7.0] * 25, 20, 2.0)
        assert_nan_prefix(mid, 19)
        assert upper[19:] == pytest.approx([7.0] * 6)
        assert lower[19:] == pytest.approx([7.0] * 6)

    def test_insufficient_data_all_nan(self):
        mid, upper, lower = boll(ramp(10), 20, 2.0)
        for line in (mid, upper, lower):
            assert all(math.isnan(v) for v in line)


# ─── ATR ───────────────────────────────────────────────────────

class TestAtr:
    def test_hand_computed(self):
        # h=[10,11,12], l=[9,10,11], c=[9.5,10.5,11.5], period=2
        # tr[1]=max(1,|11-9.5|,|10-9.5|)=1.5; tr[2]=max(1,|12-10.5|,|11-10.5|)=1.5
        # 首个 ATR=(tr[1]+tr[2])/2=1.5
        out = atr([10, 11, 12], [9, 10, 11], [9.5, 10.5, 11.5], 2)
        assert_nan_prefix(out, 2)
        assert out[2] == pytest.approx(1.5)

    def test_insufficient_data_all_nan(self):
        out = atr([10, 11], [9, 10], [9.5, 10.5], 2)
        assert all(math.isnan(v) for v in out)


# ─── CCI ───────────────────────────────────────────────────────

class TestCci:
    def test_hand_computed(self):
        # h=l=c=[1,2,3], period=3: tp=[1,2,3], mean=2, md=(1+0+1)/3=2/3
        # cci=(3-2)/(0.015*2/3)=100
        out = cci([1, 2, 3], [1, 2, 3], [1, 2, 3], 3)
        assert_nan_prefix(out, 2)
        assert out[2] == pytest.approx(100.0)

    def test_zero_deviation_returns_zero(self):
        out = cci([5.0] * 6, [5.0] * 6, [5.0] * 6, 3)
        assert out[2:] == pytest.approx([0.0] * 4)

    def test_insufficient_data_all_nan(self):
        out = cci([1, 2], [1, 2], [1, 2], 3)
        assert all(math.isnan(v) for v in out)


# ─── ADX ───────────────────────────────────────────────────────

class TestAdx:
    def test_rising_channel_plus_di_dominates(self):
        # 上升且波幅扩张：high=[1,3,5,7,9,11], low=close=[0..5], period=2
        # plus_dm 恒 2、minus_dm 恒 0 → DX=100；首个 ADX 在 period+1 下标
        high = [1, 3, 5, 7, 9, 11]
        low = [0, 1, 2, 3, 4, 5]
        close = [0, 1, 2, 3, 4, 5]
        adx_line, plus_di, minus_di = adx(high, low, close, 2)
        assert_nan_prefix(adx_line, 3)
        assert_nan_prefix(plus_di, 3)
        assert_nan_prefix(minus_di, 3)
        assert adx_line[3] == pytest.approx(100.0)
        assert minus_di[3] == pytest.approx(0.0)
        assert plus_di[3] == pytest.approx(4 / 8.5 * 100.0)  # (plus_smooth=4)/(atr_smooth=8.5)

    def test_insufficient_data_all_nan(self):
        for line in adx([1, 2, 3], [0, 1, 2], [0.5, 1.5, 2.5], 2):
            assert all(math.isnan(v) for v in line)


# ─── OBV ───────────────────────────────────────────────────────

class TestObv:
    def test_hand_computed(self):
        # close 10,11,10,10,12 + volume 100..500
        out = obv([10, 11, 10, 10, 12], [100, 200, 300, 400, 500])
        assert out == [0.0, 200.0, -100.0, -100.0, 400.0]

    def test_empty_input(self):
        assert obv([], []) == []

    def test_length_preserved(self):
        assert len(obv(ramp(7), [1.0] * 7)) == 7
