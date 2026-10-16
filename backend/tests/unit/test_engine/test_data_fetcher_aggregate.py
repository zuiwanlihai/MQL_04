"""aggregate_m1_to_tf 单测 — 覆盖固定分钟聚合 / 日历聚合 / 尾组策略 / 脏行清洗。

测试数据用 UTC epoch 秒，人工构造便于手算校验 OHLCV 聚合结果。
"""

import pytest

from app.services.backtest.data_fetcher import (
    DataUnavailableError,
    aggregate_m1_to_tf,
)

# ── 工具：快速造 N 根 M1 bars ──────────────────────────────────────────

def _make_m1(n: int, start_ts: int = 1_735_689_600) -> list[dict]:
    """start_ts 默认是 2025-01-01 00:00 UTC（周四）。"""
    bars = []
    price = 100.0
    for i in range(n):
        t = start_ts + i * 60
        drift = 0.01 if i % 2 == 0 else -0.01
        o, c = price, price + drift
        h, lo = max(o, c) + 0.02, min(o, c) - 0.02
        vol = 10 + i
        bars.append({"time": t, "open": o, "high": h, "low": lo, "close": c, "volume": vol})
        price = c
    return bars


# ── M1 → M1：仅清洗 + 字段重命名 ────────────────────────────────────────

class TestAggregateM1ToM1:
    def test_passthrough_sanitizes_and_renames(self):
        m1 = _make_m1(3)
        result = aggregate_m1_to_tf(m1, "M1")
        assert len(result) == 3
        assert result[0]["time"] == m1[0]["time"]
        assert result[0]["open"] == m1[0]["open"]
        # volume → tick_volume 字段重命名
        assert "tick_volume" in result[0]
        assert "volume" not in result[0]
        assert result[0]["tick_volume"] == m1[0]["volume"]

    def test_dirty_rows_dropped(self):
        m1 = _make_m1(5)
        # 插入脏行
        m1[1]["open"] = float("nan")
        m1[2]["high"] = 0.0  # high < low 且 h < max(o,c)
        m1[3]["low"] = float("inf")
        result = aggregate_m1_to_tf(m1, "M1")
        # 脏行 + 边界条件，最终应只剩第 0 和 4 根
        assert len(result) == 2
        assert result[0]["time"] == m1[0]["time"]
        assert result[1]["time"] == m1[4]["time"]

    def test_duplicate_times_deduped(self):
        m1 = _make_m1(3)
        m1.append({**m1[0], "close": 999.0})  # 重复 time，不同 close
        result = aggregate_m1_to_tf(m1, "M1")
        assert len(result) == 3  # 去重后
        assert all(result[i]["time"] < result[i + 1]["time"] for i in range(2))


# ── 固定分钟聚合（M5 为代表） ──────────────────────────────────────────

class TestAggregateFixed:
    def test_m5_aggregates_5_bars(self):
        m1 = _make_m1(10)  # 刚好 2 组 M5
        result = aggregate_m1_to_tf(m1, "M5")
        assert len(result) == 2

        # 第 0 组：m1[0:5]
        g0 = result[0]
        assert g0["time"] == m1[0]["time"]
        assert g0["open"] == m1[0]["open"]
        assert g0["close"] == m1[4]["close"]
        assert g0["high"] == max(b["high"] for b in m1[0:5])
        assert g0["low"] == min(b["low"] for b in m1[0:5])
        assert g0["tick_volume"] == sum(b["volume"] for b in m1[0:5])

        # 第 1 组：m1[5:10]
        g1 = result[1]
        assert g1["time"] == m1[5]["time"]
        assert g1["open"] == m1[5]["open"]
        assert g1["close"] == m1[9]["close"]

    def test_m5_tail_discarded(self):
        """13 根 M1 → 2 组完整 M5，最后 3 根（残缺）被丢弃。"""
        m1 = _make_m1(13)
        result = aggregate_m1_to_tf(m1, "M5")
        assert len(result) == 2  # 5+5=10 根入桶，最后 3 根丢弃

    def test_h1_aggregates_60_bars(self):
        m1 = _make_m1(60)
        result = aggregate_m1_to_tf(m1, "H1")
        assert len(result) == 1
        g0 = result[0]
        assert g0["time"] == m1[0]["time"]
        assert g0["open"] == m1[0]["open"]
        assert g0["close"] == m1[59]["close"]
        assert g0["tick_volume"] == sum(b["volume"] for b in m1)

    def test_h1_tail_discarded(self):
        m1 = _make_m1(65)
        result = aggregate_m1_to_tf(m1, "H1")
        assert len(result) == 1  # 60 根完整组；最后 5 根丢弃

    def test_m15_15_bars_per_group(self):
        m1 = _make_m1(45)  # 3 × M15
        result = aggregate_m1_to_tf(m1, "M15")
        assert len(result) == 3
        assert result[0]["time"] == m1[0]["time"]
        assert result[2]["close"] == m1[44]["close"]


# ── 日历聚合（D1 为代表） ─────────────────────────────────────────────

class TestAggregateCalendar:
    def test_d1_single_day(self):
        # 60*24 = 1440 根 M1 覆盖完整一天
        m1 = _make_m1(60 * 24, start_ts=1_735_689_600)
        result = aggregate_m1_to_tf(m1, "D1")
        assert len(result) == 1
        g0 = result[0]
        assert g0["time"] == m1[0]["time"]
        assert g0["open"] == m1[0]["open"]
        assert g0["close"] == m1[-1]["close"]
        assert g0["high"] == max(b["high"] for b in m1)
        assert g0["low"] == min(b["low"] for b in m1)

    def test_d1_crosses_day_boundary(self):
        """2025-01-01 23:55 + 2025-01-02 00:05 → 两个 D1 bar。"""
        # 1435 根 M1（从 00:00 到 23:54）+ 5 根（次日 00:00-00:04）
        day1 = _make_m1(1435, start_ts=1_735_689_600)
        day2 = _make_m1(5, start_ts=1_735_776_000)  # 次日 00:00 UTC
        m1 = day1 + day2
        result = aggregate_m1_to_tf(m1, "D1")
        assert len(result) == 2
        # 第一根的 time 应是 day1 起点，第二根是 day2 起点
        assert result[0]["time"] == 1_735_689_600
        assert result[1]["time"] == 1_735_776_000
        # 残缺尾组（次日只有 5 根 M1）也必须成为 D1 bar
        assert result[1]["close"] == day2[-1]["close"]

    def test_d1_incomplete_last_day_kept(self):
        """只有半天 M1 → 仍产出 1 根 D1 bar（日历类允许残缺尾组）。"""
        m1 = _make_m1(600, start_ts=1_735_689_600)  # 10 小时的 M1
        result = aggregate_m1_to_tf(m1, "D1")
        assert len(result) == 1  # 不是 0！
        assert result[0]["time"] == m1[0]["time"]


# ── 异常路径 ──────────────────────────────────────────────────────────

class TestAggregateErrors:
    def test_unsupported_tf_raises(self):
        with pytest.raises(DataUnavailableError, match="不支持的聚合目标周期"):
            aggregate_m1_to_tf([], "Y1")

    def test_too_few_m1_raises(self):
        with pytest.raises(DataUnavailableError, match="M1 数据不足"):
            aggregate_m1_to_tf(_make_m1(1), "H1")
