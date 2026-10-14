"""GatewayNode model — registered MT5 gateway processes.

The backend polls each node's ``/health`` endpoint every few seconds and
mirrors the result onto this table (status, resource usage, heartbeat time).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class GatewayNode(BaseModel, kw_only=True):
    """A gateway service instance (one per MT5 terminal host).

    ``type`` distinguishes the default python gateway (方案 A) from an EA
    bridge node (方案 B, P4): an EA node has no HTTP service — ``host`` encodes
    ``ea:{mt5_server}:{mt5_login}``, ``port`` is 0, and it authenticates sync
    calls with ``ea_key_hash`` (sha256 of the one-time node_key).
    """

    __tablename__ = "gateway_nodes"
    __table_args__ = (
        UniqueConstraint("host", "port", name="uq_gateway_nodes_host_port"),
        UniqueConstraint("ea_key_hash", name="uq_gateway_nodes_ea_key_hash"),
        CheckConstraint(
            "type IN ('python', 'ea_bridge')", name="ck_gateway_nodes_type"
        ),
    )

    name: Mapped[str] = mapped_column(
        String(64), nullable=False, default="本地网关",
        comment="Display name of the gateway node.",
    )
    type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="python", server_default="python",
        comment="python | ea_bridge (P4 EA 桥节点).",
    )
    host: Mapped[str] = mapped_column(
        String(255), nullable=False,
        comment="Hostname/IP, or 'ea:{mt5_server}:{mt5_login}' for EA nodes.",
    )
    port: Mapped[int] = mapped_column(
        Integer, nullable=False, default=8090,
        comment="TCP port of the gateway service (0 for EA nodes).",
    )
    ea_key_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None,
        comment="sha256(node_key) — X-EA-Key credential for /ea/* endpoints.",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="starting",
        comment="online | offline | degraded | starting",
    )
    version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="",
        comment="Gateway software version reported by /health.",
    )
    uptime_seconds: Mapped[float] = mapped_column(
        nullable=False, default=0.0,
        comment="Seconds since the gateway process started.",
    )
    active_sessions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Active MT5 sessions reported by the gateway (0/1 for MVP).",
    )
    cpu_percent: Mapped[float] = mapped_column(
        nullable=False, default=0.0,
        comment="Host CPU utilisation percent.",
    )
    memory_percent: Mapped[float] = mapped_column(
        nullable=False, default=0.0,
        comment="Host memory utilisation percent.",
    )
    last_heartbeat: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="Time of the last successful health poll.",
    )
    is_primary: Mapped[bool] = mapped_column(
        nullable=False, default=True,
        comment="Primary node serves market data by default.",
    )
    # ORM attribute is renamed because the declarative base reserves
    # ``metadata`` (it holds the MetaData object). DB column stays "metadata".
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default_factory=dict,
        comment="Extra info: mt5_version, os, …",
    )

    def __repr__(self) -> str:
        return f"<GatewayNode id={self.id!r} {self.host}:{self.port} {self.status}>"
