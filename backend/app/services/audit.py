"""Audit writer + chain-integrity checker (M6, PRD M7 审计 P0).

Single insert helper used by every audited endpoint/manager action.  Rows are
hash-chained (``hash = sha256(prev_hash + canonical_json)``); the nightly
``verify_chain`` walks the table and reports the first broken link.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from app.engine.session import AsyncSessionLocal
from app.models import AuditLog


@dataclass
class AuditActor:
    """Who performed the action (from the request / system)."""

    actor_type: str = "user"      # user | system | strategy
    actor_id: object = None       # uuid.UUID | None
    actor_label: str = ""
    source_ip: str = ""


def _canonical(fields: dict) -> str:
    return json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str)


def _compute_hash(prev_hash: str, payload: dict) -> str:
    return hashlib.sha256((prev_hash + _canonical(payload)).encode("utf-8")).hexdigest()


async def record(
    action: str,
    *,
    actor: AuditActor | None = None,
    target_type: str = "",
    target_id: str = "",
    params: dict | None = None,
    result: str = "success",
    error_code: str | None = None,
    risk_decision: str | None = None,
    rule_id: str | None = None,
    correlation_id: str = "",
    db=None,
) -> AuditLog:
    """Append one audit row to the hash chain (same session as the caller if given)."""
    actor = actor or AuditActor(actor_type="system")
    params = params or {}
    now = datetime.now(UTC)
    own = db is None
    ctx = AsyncSessionLocal() if own else None
    db = db or await ctx.__aenter__()  # type: ignore[union-attr]
    try:
        last = (await db.execute(
            select(AuditLog.hash).order_by(AuditLog.created_at.desc(),
                                          AuditLog.id.desc()).limit(1)
        )).scalar_one_or_none()
        prev_hash = last or ""
        payload = {
            "ts_ms": int(now.timestamp() * 1000),
            "actor_type": actor.actor_type,
            "actor_id": str(actor.actor_id) if actor.actor_id else "",
            "actor_label": actor.actor_label,
            "action": action,
            "target_type": target_type,
            "target_id": str(target_id),
            "params": params,
            "result": result,
            "error_code": error_code or "",
            "risk_decision": risk_decision or "",
            "rule_id": rule_id or "",
            "source_ip": actor.source_ip,
            "correlation_id": correlation_id,
        }
        row = AuditLog(
            ts_ms=payload["ts_ms"],
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            actor_label=actor.actor_label,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            params=params,
            result=result,
            error_code=error_code,
            risk_decision=risk_decision,
            rule_id=rule_id,
            source_ip=actor.source_ip,
            correlation_id=correlation_id,
            prev_hash=prev_hash,
            hash=_compute_hash(prev_hash, payload),
        )
        db.add(row)
        if own:
            await db.commit()
        return row
    finally:
        if own:
            await ctx.__aexit__(None, None, None)  # type: ignore[name-defined]


async def verify_chain() -> tuple[int, int]:
    """Walk the whole audit table; return (total, first_broken_index).

    ``first_broken_index`` is -1 when the chain is intact.  A break means an
    audit row was edited or deleted out-of-band → 审计链校验失败 alert (M7).
    """
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(AuditLog).order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        )).scalars().all()
    prev_hash = ""
    for i, r in enumerate(rows):
        payload = {
            "ts_ms": r.ts_ms,
            "actor_type": r.actor_type,
            "actor_id": str(r.actor_id) if r.actor_id else "",
            "actor_label": r.actor_label,
            "action": r.action,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "params": r.params,
            "result": r.result,
            "error_code": r.error_code or "",
            "risk_decision": r.risk_decision or "",
            "rule_id": r.rule_id or "",
            "source_ip": r.source_ip,
            "correlation_id": r.correlation_id,
        }
        expected = _compute_hash(prev_hash, payload)
        if r.prev_hash != prev_hash or r.hash != expected:
            return len(rows), i
        prev_hash = r.hash
    return len(rows), -1
