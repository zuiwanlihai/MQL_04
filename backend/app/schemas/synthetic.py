from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SyntheticDatasetGenerateIn(BaseModel):
    """Parameters for server-side synthetic generation (POST /generate).

    Two mutually exclusive paths:

    1. **Parameter path** (preferred for simple shapes — random / uptrend / downtrend)
       — client sends generation parameters only, backend runs numpy GBM → Parquet.
       Payload ≈ 200 bytes, generation < 2s regardless of bar count.
       ``bars`` must be ``None``.

    2. **Bars path** (for complex shapes — V / W / M / inverted-V, etc.)
       — client already produced bars locally and sends them here; backend
       just writes them to Parquet.  ``drift / seed / first_time / last_time``
       are ignored when ``bars`` is supplied.
    """
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    symbol: str = Field(default="XAUUSD", max_length=32)
    base_price: float = 1900.0
    volatility: float = 0.0005

    # ── Parameter-path fields (ignored when bars is provided) ──
    drift: float = 0.0
    first_time: int | None = Field(default=None, description="Epoch seconds, inclusive")
    last_time: int | None = Field(default=None, description="Epoch seconds, exclusive")
    seed: int | None = Field(default=None, description="Reproducible seed (optional)")

    # ── Bars-path field ──
    bars: list[dict[str, Any]] | None = Field(
        default=None,
        description="Pre-generated M1 bars [{time,open,high,low,close,volume}]. "
                    "When supplied the backend skips GBM and writes this directly to Parquet.",
    )


class SyntheticDatasetCreate(BaseModel):
    """Legacy client-side create — full bars array in JSONB. Kept for compat."""
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    symbol: str = Field(default="XAUUSD", max_length=32)
    base_price: float = 1900.0
    volatility: float = 0.0005
    first_time: int
    last_time: int
    bar_count: int
    bars: list[dict[str, Any]] = Field(..., description="Ascending M1 bars: {time,open,high,low,close,volume}")


class SyntheticDatasetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)


class SyntheticDatasetOut(BaseModel):
    id: int
    name: str
    description: str | None
    symbol: str
    base_price: float
    volatility: float
    first_time: int
    last_time: int
    bar_count: int
    storage_path: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SyntheticDatasetDetail(SyntheticDatasetOut):
    # None when the caller didn't request ?include_bars=true OR when the
    # dataset is Parquet-backed (storage_path set — JSONB bars always NULL).
    bars: list[dict[str, Any]] | None = None
