"""Strategy research (M3) models: strategies, frozen versions, templates.

``strategies.source_code`` is the *working copy* edited in the Monaco
editor; ``strategy_versions`` rows are immutable frozen snapshots taken
via ``POST /strategies/{id}/versions``.  ``current_version`` points at the
latest frozen version (``None`` while the strategy has never been frozen) —
consistency is guaranteed by the single-transaction version freeze plus the
``(strategy_id, version)`` unique constraint, not by a circular FK.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class Strategy(BaseModel, kw_only=True):
    """A user-owned Python strategy (metadata + working source copy)."""

    __tablename__ = "strategies"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_strategies_user_name"),
        Index("ix_strategies_user_status", "user_id", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="Owning platform user.",
    )
    name: Mapped[str] = mapped_column(
        String(120), nullable=False,
        comment="Strategy name, unique per user.",
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="",
        comment="Free-form description.",
    )
    language: Mapped[str] = mapped_column(
        String(16), nullable=False, default="python",
        comment="python | mql5 (MVP only python is accepted by the API).",
    )
    direction: Mapped[str] = mapped_column(
        String(16), nullable=False, default="both",
        comment="long_only | short_only | both",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft",
        comment="draft | active | paused | archived",
    )
    symbol_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default_factory=list,
        comment="Declared symbol names, e.g. ['EURUSD']. No FK — symbols "
        "come from the gateway at runtime.",
    )
    timeframe: Mapped[str] = mapped_column(
        String(8), nullable=False, default="H1",
        comment="Primary bar period (M1/M5/M15/M30/H1/H4/D1).",
    )
    tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default_factory=list,
        comment="User tags for filtering.",
    )
    current_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None,
        comment="Latest frozen version number; None = never frozen.",
    )
    source_code: Mapped[str] = mapped_column(
        Text, nullable=False, default="",
        comment="Working copy edited in the web editor. Excluded from list "
        "responses; only returned by the detail endpoint.",
    )
    source_code_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None,
        comment="SHA-256 hex of the current source_code; used by the visual "
        "builder to detect if the user manually edited the code since last "
        "builder-save. Null = old strategy migrated before this column existed.",
    )
    builder_config: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, default=None,
        comment="Visual builder block DSL (P2); None = not builder-created "
        "or user has diverged. Excluded from list responses.",
    )
    parameters: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, default=None,
        comment="Strategy-level parameter overrides editable from the params "
        "panel; merged over template defaults at run time.",
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, default=None,
        comment="User who created the strategy.",
    )

    def __repr__(self) -> str:
        return f"<Strategy id={self.id!r} name={self.name!r} status={self.status!r}>"


class StrategyVersion(BaseModel, kw_only=True):
    """Immutable frozen snapshot of a strategy's source code."""

    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uq_strategy_versions_strategy_version"),
    )

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="Parent strategy.",
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="Monotonically increasing version number (1-based).",
    )
    source_code: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="Frozen source code at this version.",
    )
    changelog: Mapped[str] = mapped_column(
        String(200), nullable=False, default="",
        comment="User-supplied note for this freeze.",
    )
    compiled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="Compile time — filled by the backtest slice (M4).",
    )
    compile_errors: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True, default=None,
        comment="Compile error list — filled by the backtest slice (M4).",
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, default=None,
        comment="User who froze this version.",
    )

    def __repr__(self) -> str:
        return f"<StrategyVersion strategy_id={self.strategy_id!r} version={self.version!r}>"


class StrategyTemplate(BaseModel, kw_only=True):
    """Built-in starter template seeded from shared/sdk/templates."""

    __tablename__ = "strategy_templates"

    name: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True,
        comment="Template name (unique).",
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="",
        comment="What the template demonstrates.",
    )
    language: Mapped[str] = mapped_column(
        String(16), nullable=False, default="python",
        comment="python | mql5.",
    )
    category: Mapped[str] = mapped_column(
        String(32), nullable=False, default="trend",
        comment="trend | mean_reversion | grid | risk_management …",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Display order in the template gallery.",
    )
    parameters: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default_factory=dict,
        comment="Default parameter map shown in the create dialog.",
    )
    source_code: Mapped[str] = mapped_column(
        Text, nullable=False, default="",
        comment="Template source code (read-only).",
    )

    def __repr__(self) -> str:
        return f"<StrategyTemplate name={self.name!r} category={self.category!r}>"
