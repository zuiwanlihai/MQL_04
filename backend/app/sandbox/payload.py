"""Backtest task payload serialisation (M4).

The parent process (queue worker) assembles a :class:`BacktestPayload` plus
:class:`BarArrays` and writes them to the per-task runtime directory as
``payload.json`` + ``bars.npz``.  The runner subprocess reads them back —
this is the ONLY channel from parent to child (no DB credentials cross the
boundary).

``describe_execution_mode`` is the single source of truth for the
price-formula assumption text frozen into ``config_snapshot`` and shown in
the pre-submission confirmation dialog (PRD M4 P0).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

PAYLOAD_JSON = "payload.json"
PAYLOAD_NPZ = "bars.npz"

# Default adverse slippage per tier, in points (PRD M4 撮合三档).
MODE_DEFAULT_SLIPPAGE: dict[str, float] = {
    "conservative": 2.0,
    "standard": 1.0,
    "optimistic": 0.0,
}

_MODE_ASSUMPTION_TEXT: dict[str, str] = {
    "conservative": (
        "保守档：信号在当根收盘产生，于次根 K 线开盘成交。"
        "开多=次根开盘价+点差+滑点；开空=次根开盘价-滑点；"
        "平多=次根开盘价-滑点；平空=次根开盘价+点差+滑点。"
        "默认滑点 2 点；最后一根 K 线的挂单被拒绝（MARKET_CLOSED）。"
    ),
    "standard": (
        "标准档：以当根收盘价成交。"
        "买入=收盘价+点差+滑点；卖出=收盘价-滑点。默认滑点 1 点。"
    ),
    "optimistic": (
        "乐观档：以当根收盘价成交，不计点差与滑点。"
        "结果偏乐观，不具备实盘参考意义。"
    ),
}


def describe_execution_mode(mode: str) -> str:
    """Chinese price-formula assumption text for the given tier."""
    return _MODE_ASSUMPTION_TEXT[mode]


@dataclass
class BacktestPayload:
    """Everything the runner needs to replay one backtest (JSON-safe)."""

    task_id: str
    strategy_id: str
    source_code: str
    execution_mode: str  # conservative | standard | optimistic
    symbol: str
    timeframe: str
    tf_seconds: int
    initial_deposit: float
    leverage: int
    spread_mode: str = "current"  # current | fixed
    spread_points: float | None = None
    spread_snapshot: float = 0.0  # fallback points when a bar carries no spread
    swap_long: float = 0.0  # overnight swap, points per lot per night (buy)
    swap_short: float = 0.0  # overnight swap, points per lot per night (sell)
    commission_per_lot: float = 0.0
    slippage_points: float | None = None  # None -> tier default
    warmup_bars: int = 100
    parameters: dict = field(default_factory=dict)
    seed: int = 0
    symbol_info: dict = field(default_factory=dict)
    config_snapshot: dict = field(default_factory=dict)
    callback_timeout_seconds: float = 5.0
    on_init_timeout_seconds: float = 60.0
    stop_out_level: float = 50.0
    log_limit: int = 5000
    has_on_tick: bool = False  # paper/live only: strategy defines on_tick

    @property
    def slippage_points_or_default(self) -> float:
        if self.slippage_points is not None:
            return float(self.slippage_points)
        return MODE_DEFAULT_SLIPPAGE[self.execution_mode]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BarArrays:
    """Columnar bar storage (ascending time, epoch seconds)."""

    time: np.ndarray        # int64
    open: np.ndarray        # float64
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    tick_volume: np.ndarray  # int64
    spread: np.ndarray       # int64 (points; 0 = unknown -> use snapshot)

    def __len__(self) -> int:
        return int(self.time.shape[0])

    def at(self, i: int) -> tuple[int, float, float, float, float, int, int]:
        return (
            int(self.time[i]),
            float(self.open[i]),
            float(self.high[i]),
            float(self.low[i]),
            float(self.close[i]),
            int(self.tick_volume[i]),
            int(self.spread[i]),
        )


def save_payload(
    payload: BacktestPayload, bars: BarArrays, directory: Path
) -> Path:
    """Write payload.json + bars.npz under ``directory``; return the path."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / PAYLOAD_JSON).write_text(
        json.dumps(payload.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    np.savez(
        directory / PAYLOAD_NPZ,
        time=bars.time.astype("int64"),
        open=bars.open.astype("float64"),
        high=bars.high.astype("float64"),
        low=bars.low.astype("float64"),
        close=bars.close.astype("float64"),
        tick_volume=bars.tick_volume.astype("int64"),
        spread=bars.spread.astype("int64"),
    )
    return directory


def load_payload(directory: Path) -> tuple[BacktestPayload, BarArrays]:
    """Read a payload directory back into (meta, bars)."""
    meta = json.loads((directory / PAYLOAD_JSON).read_text(encoding="utf-8"))
    payload = BacktestPayload(**meta)
    with np.load(directory / PAYLOAD_NPZ) as f:
        bars = BarArrays(
            time=np.ascontiguousarray(f["time"]),
            open=np.ascontiguousarray(f["open"]),
            high=np.ascontiguousarray(f["high"]),
            low=np.ascontiguousarray(f["low"]),
            close=np.ascontiguousarray(f["close"]),
            tick_volume=np.ascontiguousarray(f["tick_volume"]),
            spread=np.ascontiguousarray(f["spread"]),
        )
    return payload, bars
