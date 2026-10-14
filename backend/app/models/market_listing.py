"""Strategy marketplace (PRD Phase 3) models: shared strategy listings.

A :class:`MarketListing` is a **frozen snapshot** of a strategy taken at
publish time — later edits to the original strategy never leak into the
marketplace.  ``strategy_id`` keeps provenance but is ``SET NULL`` on delete
so removing the source strategy doesn't wipe shared listings.

Legal boundary (PRD: 仅工具分享，不涉及信号售卖): publishing requires an
explicit disclaimer acknowledgement (schema-level), every mutation is
audited, and listings are soft-deleted (``status="removed"``) rather than
physically deleted so audit trails stay meaningful.
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class MarketListing(BaseModel, kw_only=True):
    """A published (frozen) strategy snapshot visible to all users."""

    __tablename__ = "market_listings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'removed')",
            name="ck_market_listings_status",
        ),
        CheckConstraint(
            "direction IN ('long_only', 'short_only', 'both')",
            name="ck_market_listings_direction",
        ),
        Index("ix_market_listings_status_created", "status", "created_at"),
        Index("ix_market_listings_author", "author_id"),
        Index("ix_market_listings_source_hash", "source_hash"),
    )

    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategies.id", ondelete="SET NULL"),
        nullable=True, default=None,
        comment="Source strategy (provenance only; snapshot is independent).",
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="Publisher (strategy owner at publish time).",
    )
    strategy_name: Mapped[str] = mapped_column(
        String(120), nullable=False,
        comment="Frozen strategy name at publish time.",
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="",
        comment="Frozen description.",
    )
    language: Mapped[str] = mapped_column(
        String(16), nullable=False, default="python",
        comment="python | mql5 (MVP only python).",
    )
    direction: Mapped[str] = mapped_column(
        String(16), nullable=False, default="both",
        comment="long_only | short_only | both",
    )
    symbol_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default_factory=list,
        comment="Frozen declared symbols, e.g. ['EURUSD'].",
    )
    timeframe: Mapped[str] = mapped_column(
        String(8), nullable=False, default="H1",
        comment="Primary bar period (M1/M5/M15/M30/H1/H4/D1).",
    )
    tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default_factory=list,
        comment="Frozen tags for filtering.",
    )
    source_code: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="Frozen source code — never affected by later strategy edits.",
    )
    source_hash: Mapped[str] = mapped_column(
        String(16), nullable=False,
        comment="sha256(source_code)[:16] — duplicate-publish lookup (no DB "
        "unique constraint: re-publish after removal is allowed, and "
        "identical code from different authors is legitimate).",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active",
        comment="active | removed (soft delete).",
    )
    import_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="How many times this listing was imported by anyone.",
    )

    def __repr__(self) -> str:
        return (
            f"<MarketListing id={self.id!r} name={self.strategy_name!r} "
            f"status={self.status!r}>"
        )
