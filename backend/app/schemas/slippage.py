"""Slippage calibration + backtest-vs-live deviation report schemas (M4 P1).

Calibration aggregates REAL live fills (never paper — paper slippage is the
configured value itself, a circular source) per (user, symbol) and recommends
a conservative default slippage for future backtests.  The deviation report
decomposes each live fill's gap between actual and theoretical execution into
slippage / spread / request-time drift.  All numerics are floats (JSON
numbers) per the project's Out-schema convention.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CalibrationSourceOut(BaseModel):
    """Per-account contribution to a symbol's calibration sample."""

    model_config = ConfigDict(from_attributes=True)

    account_id: str
    account_label: str
    run_count: int
    sample_count: int


class SlippageCalibrationOut(BaseModel):
    """Aggregated real-fill slippage stats for one (user, symbol)."""

    model_config = ConfigDict(from_attributes=True)

    symbol: str
    sample_count: int
    skipped_count: int = Field(
        0, description="Fills without a request-time quote snapshot (old rows)."
    )
    mean_points: float | None = None
    median_points: float | None = None
    p75_points: float | None = None
    p95_points: float | None = None
    max_points: float | None = None
    recommended_points: float | None = Field(
        None, description="ceil(p95); None while the sample is insufficient."
    )
    slippage_cost: float = Field(
        0.0, description="Net adverse slippage cost in account currency."
    )
    first_deal_time: datetime | None = None
    last_deal_time: datetime | None = None
    insufficient: bool = Field(
        False, description="sample_count < 5 — do not auto-fill backtests."
    )
    sources: list[CalibrationSourceOut] = Field(default_factory=list)


class DeviationTradeOut(BaseModel):
    """One live deal compared against its theoretical execution."""

    model_config = ConfigDict(from_attributes=True)

    deal_ticket: int
    direction: str
    entry: str
    volume: float
    open_time: datetime
    signal_bar_time: datetime | None = None
    signal_price: float | None = None
    signal_spread_points: float | None = None
    theoretical_price: float | None = None
    actual_price: float
    total_deviation_points: float | None = None
    slippage_points: float | None = None
    actual_spread_points: float | None = None
    drift_points: float | None = None
    latency_seconds: float | None = None
    cost: float = Field(0.0, description="Deviation cost (account currency).")


class DeviationReportOut(BaseModel):
    """Per-run execution-quality report: actual vs theoretical fills."""

    model_config = ConfigDict(from_attributes=True)

    run_id: str
    symbol: str
    point: float
    sample_count: int
    skipped_count: int
    avg_total_deviation_points: float | None = None
    median_total_deviation_points: float | None = None
    p95_total_deviation_points: float | None = None
    avg_slippage_points: float | None = None
    avg_signal_spread_points: float | None = None
    avg_actual_spread_points: float | None = None
    avg_latency_seconds: float | None = None
    total_deviation_cost: float = 0.0
    verdict: str = Field(
        "insufficient",
        description="aligned (≤2pts) | moderate (≤10pts) | divergent | insufficient",
    )
    trades: list[DeviationTradeOut] = Field(default_factory=list)
