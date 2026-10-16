"""Synthetic market data generator — numpy-vectorised geometric Brownian motion.

Why numpy? A Python for-loop over 561,600 bars takes ~15s. The numpy vectorised
GBM below produces the same numbers in <2s, and writes ~3MB Parquet instead of
a 34MB JSONB payload.

The ``M1Generator`` is the single source of truth for synthetic OHLCV; both
the ``POST /synthetic-datasets/generate`` endpoint and any future CLI should
go through it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class GeneratedM1:
    """Result of ``generate_m1`` — bar count + first/last timestamps."""
    bar_count: int
    first_time: int          # epoch seconds
    last_time: int           # epoch seconds


def generate_m1_to_parquet(
    *,
    out_path: Path,
    start_ts: int,
    end_ts: int,
    base_price: float,
    volatility: float = 0.0005,
    drift: float = 0.0,
    seed: int | None = None,
) -> GeneratedM1:
    """Generate M1 OHLCV bars over ``[start_ts, end_ts)`` and write to Parquet.

    Parameters
    ----------
    out_path:
        Destination ``.parquet`` file. Parent dir is created if missing.
    start_ts / end_ts:
        Inclusive start, exclusive end (epoch SECONDS). ``end_ts - start_ts``
        must be divisible by 60.
    base_price:
        Starting price of the first bar.
    volatility:
        Per-bar Gaussian volatility (log-space), e.g. 0.0005 = 0.05%.
    drift:
        Deterministic per-bar log-drift (default 0 — unbiased random walk).
    seed:
        Optional ``np.random.default_rng(seed)`` for reproducible datasets.
    """
    n_minutes = int((end_ts - start_ts) // 60)
    if n_minutes <= 0:
        raise ValueError("end_ts must be strictly greater than start_ts")
    if n_minutes > 2_000_000:
        raise ValueError(f"refusing to generate {n_minutes} bars (max 2,000,000)")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(seed)

    # ── Vectorised GBM ──────────────────────────────────────────────────
    # Standard GBM: dS/S = μ dt + σ dW.  Discretised over dt=1min:
    #   log_S[t+1] = log_S[t] + drift + σ · N(0,1)
    log_returns = rng.normal(loc=drift, scale=volatility, size=n_minutes)
    log_prices = np.log(base_price) + np.cumsum(log_returns)
    closes = np.exp(log_prices)

    open_ = np.empty(n_minutes, dtype=np.float64)
    open_[0] = base_price
    open_[1:] = closes[:-1]

    # Intra-bar spread: randomised around |log_return| + volatility floor,
    # so high-low range reflects both the drift and the noise.
    spread = np.abs(log_returns) * 0.6 + volatility * 0.3
    highs = np.maximum(open_, closes) * (1.0 + spread)
    lows = np.minimum(open_, closes) * (1.0 - spread)
    volumes = rng.integers(50, 250, size=n_minutes, dtype=np.int32)

    times = np.arange(start_ts, start_ts + n_minutes * 60, 60, dtype=np.int64)

    # ── Write Parquet ───────────────────────────────────────────────────
    table = pa.table({
        "time":    pa.array(times,  type=pa.int64()),
        "open":    pa.array(open_,  type=pa.float64()),
        "high":    pa.array(highs,  type=pa.float64()),
        "low":     pa.array(lows,   type=pa.float64()),
        "close":   pa.array(closes, type=pa.float64()),
        "volume":  pa.array(volumes,type=pa.int32()),
    })
    pq.write_table(table, out_path, compression="zstd")

    return GeneratedM1(
        bar_count=n_minutes,
        first_time=int(times[0]),
        last_time=int(times[-1]),
    )


# ---------------------------------------------------------------------------
# Reader — shared by backtest queue and paper manager
# ---------------------------------------------------------------------------

def load_parquet_bars(path: str | Path) -> list[dict]:
    """Read a synthetic Parquet file back as a list of ``{time,open,high,low,close,volume}``.

    Returns plain Python dicts (not numpy arrays) because downstream
    ``aggregate_m1_to_tf`` / ``bars_from_local`` expect the old shape.
    Callers should prefer the Parquet path over JSONB for files > 50 MB.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"synthetic parquet missing: {path}")
    table = pq.read_table(path)
    cols = {name: table[name].to_numpy() for name in table.column_names}
    n = table.num_rows
    out = [None] * n
    for i in range(n):
        out[i] = {
            "time":    int(cols["time"][i]),
            "open":    float(cols["open"][i]),
            "high":    float(cols["high"][i]),
            "low":     float(cols["low"][i]),
            "close":   float(cols["close"][i]),
            "volume":  int(cols["volume"][i]),
        }
    return out


def parquet_file_size(path: str | Path) -> int:
    return Path(path).stat().st_size


# ---------------------------------------------------------------------------
# Runtime-dir resolution (caller convenience)
# ---------------------------------------------------------------------------

def resolve_synthetic_path(storage_path: str | Path, *, runtime_dir: str | Path) -> Path:
    """Turn a ``storage_path`` from the DB into an absolute filesystem path.

    Accepts either:
      - a relative path like ``"42.parquet"`` → joined under ``runtime_dir``
      - an absolute path                            → returned as-is
    """
    p = Path(storage_path)
    if p.is_absolute():
        return p
    return Path(runtime_dir) / p


def write_bars_to_parquet(
    bars: list[dict],
    *,
    out_path: Path | str,
) -> GeneratedM1:
    """Persist a pre-generated bars list to Parquet (zstd compressed).

    Used when the client already has bars (e.g. complex shapes produced
    by the FE local generator) and just wants the backend to store them
    as Parquet instead of JSONB.

    The ``bars`` list must be ascending by time and each element must carry
    ``time / open / high / low / close / volume``.  No shape validation —
    the caller is trusted.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n = len(bars)
    if n == 0:
        raise ValueError("bars list is empty")

    times   = np.empty(n, dtype=np.int64)
    opens   = np.empty(n, dtype=np.float64)
    highs   = np.empty(n, dtype=np.float64)
    lows    = np.empty(n, dtype=np.float64)
    closes  = np.empty(n, dtype=np.float64)
    volumes = np.empty(n, dtype=np.int32)

    for i, bar in enumerate(bars):
        times[i]   = int(bar["time"])
        opens[i]   = float(bar["open"])
        highs[i]   = float(bar["high"])
        lows[i]    = float(bar["low"])
        closes[i]  = float(bar["close"])
        volumes[i] = int(bar["volume"])

    table = pa.table({
        "time":    pa.array(times,   type=pa.int64()),
        "open":    pa.array(opens,   type=pa.float64()),
        "high":    pa.array(highs,   type=pa.float64()),
        "low":     pa.array(lows,    type=pa.float64()),
        "close":   pa.array(closes,  type=pa.float64()),
        "volume":  pa.array(volumes, type=pa.int32()),
    })
    pq.write_table(table, out_path, compression="zstd")

    return GeneratedM1(
        bar_count=n,
        first_time=int(times[0]),
        last_time=int(times[-1]),
    )


def storage_path_for_id(dataset_id: int) -> str:
    """Canonical relative Parquet filename for a synthetic dataset id."""
    return f"dataset_{dataset_id}.parquet"


def load_synthetic_bars(dataset, *, runtime_dir: str | Path) -> list[dict]:
    """Read M1 bars from a SyntheticDataset — Parquet if possible, else JSONB fallback.

    This is the **single entry point** for backtest queue and paper manager.
    Adding new storage backends (e.g. object-store) only requires editing this
    function; callers remain unchanged.
    """
    storage_path = getattr(dataset, "storage_path", None)
    if storage_path:
        abs_path = resolve_synthetic_path(storage_path, runtime_dir=runtime_dir)
        return load_parquet_bars(abs_path)

    # Legacy path: bars stored inline as JSONB.
    bars = getattr(dataset, "bars", None)
    if not bars:
        raise ValueError(
            f"synthetic dataset #{dataset.id} has neither storage_path nor bars"
        )
    return list(bars)
