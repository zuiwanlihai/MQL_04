"""GatewayHealthLog model — sampled heartbeat history.

Rows are written only on status change or at most every
``HEALTH_LOG_SAMPLE_INTERVAL`` seconds, and pruned after
``HEALTH_LOG_RETENTION_HOURS`` — see ``app.services.gateway_heartbeat``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class GatewayHealthLog(BaseModel, kw_only=True):
    """One sampled health observation for a gateway node."""

    __tablename__ = "gateway_health_logs"

    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gateway_nodes.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="Gateway node this observation belongs to.",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False,
        comment="online | offline | degraded at observation time.",
    )
    latency_ms: Mapped[float] = mapped_column(
        nullable=False, default=0.0,
        comment="Round-trip latency of the /health poll.",
    )
    error_rate: Mapped[float] = mapped_column(
        nullable=False, default=0.0,
        comment="Failed-poll ratio over the recent sliding window.",
    )
    message: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None,
        comment="Human-readable detail (error text / status note).",
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True,
        server_default=None,
        default_factory=lambda: datetime.now(timezone.utc),
        comment="Observation time.",
    )

    def __repr__(self) -> str:
        return f"<GatewayHealthLog node_id={self.node_id!r} {self.status}>"
