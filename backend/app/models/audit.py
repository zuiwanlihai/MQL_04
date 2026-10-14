"""Audit log (M6, PRD M7 审计 P0): write-once, hash-chained action trail.

Every operation that can move money, change permissions or alter system
behaviour must leave a line here.  Tamper evidence: ``hash = sha256(prev_hash
+ canonical_json(fields))`` — a daily integrity job re-walks the chain and any
edit/deletion breaks every subsequent hash (alerted as 审计链校验失败).

The DB role for production should be INSERT/SELECT-only; the application never
updates or deletes audit rows.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Identity, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AuditLog(BaseModel, kw_only=True):
    """One immutable audited action (closed-set action enum, see services/audit)."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_actor_time", "actor_id", "created_at"),
        Index("ix_audit_logs_action_time", "action", "created_at"),
        Index("ix_audit_logs_target", "target_type", "target_id"),
    )

    seq: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), unique=True, nullable=False,
        init=False, sort_order=-90,
        comment="插入顺序 = 链的遍历顺序。id 是 uuid4、created_at 只有微秒"
                "精度，两者都排不出稳定的先后，而哈希链的校验顺序错一步就"
                "会被读成「有人改了审计」。",
    )
    ts_ms: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0,
        comment="UTC millisecond timestamp (M3 ⑤ time contract).",
    )
    actor_type: Mapped[str] = mapped_column(
        String(8), nullable=False, default="user",
        comment="user | system | strategy.",
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, default=None,
        comment="User id (None for system/strategy actors).",
    )
    actor_label: Mapped[str] = mapped_column(
        String(120), nullable=False, default="",
        comment="Human-readable actor (email / strategy name + version).",
    )
    action: Mapped[str] = mapped_column(
        String(48), nullable=False,
        comment="Closed enum: login, logout, account_bind, live_deploy, "
        "live_pause, live_resume, live_stop, live_reset, admission_pass, "
        "kill_switch, risk_trigger, demote, re_admit, reconcile_mismatch, "
        "rule_change, flag_change, sandbox_violation, ...",
    )
    target_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default="",
        comment="user | strategy | live_run | mt5_account | risk_rule | system.",
    )
    target_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="Target object id."
    )
    params: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default_factory=dict,
        comment="Action parameters (old→new for config changes).",
    )
    result: Mapped[str] = mapped_column(
        String(16), nullable=False, default="success",
        comment="success | failure | blocked.",
    )
    error_code: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None
    )
    risk_decision: Mapped[str | None] = mapped_column(
        String(24), nullable=True, default=None,
        comment="allow | reject | halt_open_today | close_all_and_stop.",
    )
    rule_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    source_ip: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="Request origin."
    )
    correlation_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="",
        comment="Signal→order→deal chain id.",
    )
    prev_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="",
        comment="Hash of the previous row (empty string for the chain head).",
    )
    hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="",
        comment="sha256(prev_hash + canonical payload) — tamper evidence.",
    )

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id!r} action={self.action!r} actor={self.actor_label!r}>"
