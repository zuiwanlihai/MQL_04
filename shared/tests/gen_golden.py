"""重新生成 shared/tests/fixtures/indicators_golden.json（P2-7 镜像对照）。

golden 向量由本脚本从 shared/sdk/indicators.py 生成，两侧共同断言：
  - Python 侧：shared/tests/test_golden_mirror.py
  - 前端 TS：frontend/src/lib/indicators.mirror.test.ts

仅在**有意**修改指标口径后手动重跑：
    python shared/tests/gen_golden.py
重跑后前端 TS 侧必须仍然全绿——红掉即意味着两侧实现已漂移。
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 使 `import sdk` 可用

from sdk.indicators import boll, ema, kdj, macd, rsi, sma  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "indicators_golden.json"

N_BARS = 80
SEED = 20261014
PARAMS = {
    "sma_period": 20,
    "ema_period": 12,
    "boll_period": 20,
    "boll_mult": 2,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "rsi_period": 14,
    "kdj_period": 9,
    "kdj_smooth_k": 3,
    "kdj_smooth_d": 3,
}


def build_bars() -> list[dict]:
    """确定性随机游走 80 根 1h K 线（价格量级贴近 XAUUSD 实盘）。"""
    rng = random.Random(SEED)
    bars: list[dict] = []
    price = 2345.60
    for i in range(N_BARS):
        open_ = round(price, 2)
        close = round(price + rng.uniform(-1.2, 1.2) + 0.05, 2)
        high = round(max(open_, close) + rng.uniform(0.0, 0.6), 2)
        low = round(min(open_, close) - rng.uniform(0.0, 0.6), 2)
        bars.append(
            {
                "time": 1_760_000_000 + i * 3600,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
            }
        )
        price = close
    return bars


def points(bars: list[dict], values: list[float]) -> list[dict]:
    """去掉 NaN 前缀 → [{"time", "value"}]，与前端的 tail-only 输出对齐。"""
    return [
        {"time": b["time"], "value": v}
        for b, v in zip(bars, values)
        if not math.isnan(v)
    ]


def main() -> None:
    bars = build_bars()
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]

    dif, dea, hist = macd(
        closes, PARAMS["macd_fast"], PARAMS["macd_slow"], PARAMS["macd_signal"]
    )
    k, d, j = kdj(
        highs, lows, closes, PARAMS["kdj_period"], PARAMS["kdj_smooth_k"], PARAMS["kdj_smooth_d"]
    )
    mid, upper, lower = boll(closes, PARAMS["boll_period"], PARAMS["boll_mult"])

    fixture = {
        "_comment": (
            "P2-7 镜像 golden：由 shared/tests/gen_golden.py 生成，"
            "Python 与前端 TS 两侧共同断言，勿手改。"
        ),
        "params": PARAMS,
        "bars": bars,
        "expected": {
            "sma": points(bars, sma(closes, PARAMS["sma_period"])),
            "ema": points(bars, ema(closes, PARAMS["ema_period"])),
            "boll": {
                "mid": points(bars, mid),
                "upper": points(bars, upper),
                "lower": points(bars, lower),
            },
            "macd": {
                "dif": points(bars, dif),
                "dea": points(bars, dea),
                "hist": points(bars, hist),
            },
            "rsi": points(bars, rsi(closes, PARAMS["rsi_period"])),
            "kdj": {"k": points(bars, k), "d": points(bars, d), "j": points(bars, j)},
        },
    }

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(fixture, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE.relative_to(FIXTURE.parents[3])} ({len(bars)} bars)")


if __name__ == "__main__":
    main()
