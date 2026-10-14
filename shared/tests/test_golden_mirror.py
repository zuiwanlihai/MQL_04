"""P2-7 镜像对照（Python 侧）：SDK 输出必须逐点复现 golden fixture。

前端 TS 侧在 frontend/src/lib/indicators.mirror.test.ts 断言**同一份** fixture：
只改一侧口径、不改另一侧，两边都会红。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from sdk.indicators import boll, ema, kdj, macd, rsi, sma

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "indicators_golden.json"

# 前端图表只镜像这 6 个（ATR/CCI/ADX/OBV/WMA 为 SDK 独有，无镜像）
MIRRORED = {"sma", "ema", "boll", "macd", "rsi", "kdj"}


@pytest.fixture(scope="module")
def golden() -> dict:
    if not FIXTURE.exists():
        pytest.fail(f"缺 golden fixture {FIXTURE} — 先跑 python shared/tests/gen_golden.py")
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def tail_points(times: list[int], values: list[float]) -> list[dict]:
    """NaN 前缀 → [{"time", "value"}]，与前端 tail-only 输出对齐。"""
    return [{"time": t, "value": v} for t, v in zip(times, values) if not math.isnan(v)]


def assert_points_match(actual: list[dict], expected: list[dict]) -> None:
    assert len(actual) == len(expected), f"点数不同: {len(actual)} != {len(expected)}"
    for a, e in zip(actual, expected):
        assert a["time"] == e["time"]
        assert a["value"] == pytest.approx(e["value"], rel=1e-9, abs=1e-9)


def test_fixture_covers_exactly_the_mirrored_set(golden: dict) -> None:
    assert set(golden["expected"]) == MIRRORED


def test_sma(golden: dict) -> None:
    times = [b["time"] for b in golden["bars"]]
    closes = [b["close"] for b in golden["bars"]]
    out = sma(closes, golden["params"]["sma_period"])
    assert_points_match(tail_points(times, out), golden["expected"]["sma"])


def test_ema(golden: dict) -> None:
    times = [b["time"] for b in golden["bars"]]
    closes = [b["close"] for b in golden["bars"]]
    out = ema(closes, golden["params"]["ema_period"])
    assert_points_match(tail_points(times, out), golden["expected"]["ema"])


def test_boll(golden: dict) -> None:
    times = [b["time"] for b in golden["bars"]]
    closes = [b["close"] for b in golden["bars"]]
    mid, upper, lower = boll(closes, golden["params"]["boll_period"], golden["params"]["boll_mult"])
    assert_points_match(tail_points(times, mid), golden["expected"]["boll"]["mid"])
    assert_points_match(tail_points(times, upper), golden["expected"]["boll"]["upper"])
    assert_points_match(tail_points(times, lower), golden["expected"]["boll"]["lower"])


def test_macd(golden: dict) -> None:
    times = [b["time"] for b in golden["bars"]]
    closes = [b["close"] for b in golden["bars"]]
    p = golden["params"]
    dif, dea, hist = macd(closes, p["macd_fast"], p["macd_slow"], p["macd_signal"])
    assert_points_match(tail_points(times, dif), golden["expected"]["macd"]["dif"])
    assert_points_match(tail_points(times, dea), golden["expected"]["macd"]["dea"])
    assert_points_match(tail_points(times, hist), golden["expected"]["macd"]["hist"])


def test_rsi(golden: dict) -> None:
    times = [b["time"] for b in golden["bars"]]
    closes = [b["close"] for b in golden["bars"]]
    out = rsi(closes, golden["params"]["rsi_period"])
    assert_points_match(tail_points(times, out), golden["expected"]["rsi"])


def test_kdj(golden: dict) -> None:
    times = [b["time"] for b in golden["bars"]]
    highs = [b["high"] for b in golden["bars"]]
    lows = [b["low"] for b in golden["bars"]]
    closes = [b["close"] for b in golden["bars"]]
    p = golden["params"]
    k, d, j = kdj(highs, lows, closes, p["kdj_period"], p["kdj_smooth_k"], p["kdj_smooth_d"])
    assert_points_match(tail_points(times, k), golden["expected"]["kdj"]["k"])
    assert_points_match(tail_points(times, d), golden["expected"]["kdj"]["d"])
    assert_points_match(tail_points(times, j), golden["expected"]["kdj"]["j"])
