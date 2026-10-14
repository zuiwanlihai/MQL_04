"""Overfit-guard analyses (PRD Phase 2): Walk-Forward + Monte Carlo.

``backtest_analyses`` rows hang off a completed source backtest task:

- ``kind="monte_carlo"``  — pure post-processing of the trade PnL sequence
  (bootstrap / shuffle resampling); computed synchronously at creation and
  cached into ``result``.
- ``kind="walk_forward"`` — splits the source date range into folds and
  spawns 2×folds real child ``BacktestTask`` rows (in-sample + out-of-sample
  per fold, same frozen config).  Aggregation happens on read: once every
  child is terminal the summary is computed and cached into ``result``.
- ``kind="optimization"`` — grid / random parameter search: spawns one real
  child task per parameter combination (grid product or seeded random
  sample); aggregation on read ranks every combination by the chosen
  objective metric and caches the ranking into ``result``.

State machine: pending (unused, reserved) → running → completed | failed.
``result``/``error`` are written once (terminal states immutable, same
convention as backtest tasks).
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel

ANALYSIS_WALK_FORWARD = "walk_forward"
ANALYSIS_MONTE_CARLO = "monte_carlo"
ANALYSIS_OPTIMIZATION = "optimization"

ANALYSIS_RUNNING = "running"
ANALYSIS_COMPLETED = "completed"
ANALYSIS_FAILED = "failed"


class BacktestAnalysis(BaseModel, kw_only=True):
    """One overfit-guard analysis (Walk-Forward or Monte Carlo)."""

    __tablename__ = "backtest_analyses"
    __table_args__ = (
        Index("ix_backtest_analyses_user_created", "user_id", "created_at"),
        Index("ix_backtest_analyses_task", "task_id"),
        CheckConstraint(
            "kind IN ('walk_forward', 'monte_carlo', 'optimization')",
            name="ck_backtest_analyses_kind",
        ),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_backtest_analyses_status",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="Owning platform user.",
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("backtest_tasks.id", ondelete="CASCADE"),
        nullable=False,
        comment="Source backtest task (config template).",
    )
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False,
        comment="walk_forward | monte_carlo | optimization.",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ANALYSIS_RUNNING,
        comment="running | completed | failed.",
    )
    params: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default_factory=dict,
        comment="Run parameters + WF child task window mapping.",
    )
    result: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, default=None,
        comment="Aggregated analysis output (cached on terminal).",
    )
    error: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None,
        comment="User-facing failure detail (WF child failure etc.).",
    )

    def __repr__(self) -> str:
        return (
            f"<BacktestAnalysis id={self.id!r} kind={self.kind!r} "
            f"status={self.status!r}>"
        )
