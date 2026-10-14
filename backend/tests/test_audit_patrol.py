"""Audit-chain patrol tests (M16 审计链定时巡检).

Tamper one audit row (restore afterwards) and assert the patrol:
  * intact chain → audit_patrol row, no alert;
  * broken chain → critical alert exactly once (transition), then silent;
  * repaired chain → recovery info alert.
"""

from __future__ import annotations

import uuid

import app.services.audit_patrol as ap


def _auth_client(client):
    """Register a user, return (headers, uid)."""
    email = f"ap-{uuid.uuid4().hex[:10]}@test.com"
    r = client.post(
        "/api/v1/auth/register",
        json={"email": email, "username": f"ap{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "巡检测试"},
    )
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    from jose import jwt

    from app.config import settings

    uid = jwt.decode(token, settings.JWT_SECRET_KEY,
                     algorithms=[settings.JWT_ALGORITHM])["sub"]
    return {"Authorization": f"Bearer {token}"}, uid


def _promote_admin(client, uid: str) -> None:
    async def _up():
        from sqlalchemy import update

        from app.engine.session import AsyncSessionLocal
        from app.models import User

        async with AsyncSessionLocal() as db:
            await db.execute(update(User).where(User.id == uuid.UUID(uid))
                             .values(role="admin"))
            await db.commit()
    client.portal.call(_up)


def _count_rows(client, *, source: str | None = None,
                action: str | None = None) -> int:
    async def _q():
        from sqlalchemy import func, select

        from app.engine.session import AsyncSessionLocal
        from app.models import AlertEvent, AuditLog

        async with AsyncSessionLocal() as db:
            if source is not None:
                q = (select(func.count()).select_from(AlertEvent)
                     .where(AlertEvent.source == source))
            else:
                q = (select(func.count()).select_from(AuditLog)
                     .where(AuditLog.action == action))
            return (await db.execute(q)).scalar_one()
    return client.portal.call(_q)


def _chain_alerts(client) -> list[dict]:
    async def _q():
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AlertEvent

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(AlertEvent)
                .where(AlertEvent.source == "audit_chain")
                .order_by(AlertEvent.created_at.asc(), AlertEvent.id.asc())
            )).scalars().all()
            return [{"level": r.level, "title": r.title} for r in rows]
    return client.portal.call(_q)


def _tamper_helpers(client):
    """Return (pick_victim, tamper, restore) callables over one early row."""
    state: dict = {}

    def pick_victim():
        async def _pick():
            from sqlalchemy import select

            from app.engine.session import AsyncSessionLocal
            from app.models import AuditLog

            async with AsyncSessionLocal() as db:
                row = (await db.execute(
                    select(AuditLog).order_by(AuditLog.created_at.asc(),
                                              AuditLog.id.asc())
                    .offset(1).limit(1)
                )).scalars().first()
                assert row is not None
                state["id"] = row.id
                state["params"] = row.params
        client.portal.call(_pick)

    def tamper():
        async def _t():
            from sqlalchemy import update

            from app.engine.session import AsyncSessionLocal
            from app.models import AuditLog

            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(AuditLog).where(AuditLog.id == state["id"])
                    .values(params={"tampered": True})
                )
                await db.commit()
        client.portal.call(_t)

    def restore():
        async def _r():
            from sqlalchemy import update

            from app.engine.session import AsyncSessionLocal
            from app.models import AuditLog

            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(AuditLog).where(AuditLog.id == state["id"])
                    .values(params=state["params"])
                )
                await db.commit()
        client.portal.call(_r)

    return pick_victim, tamper, restore


def test_patrol_intact_no_alert(client):
    ap.reset_state()
    _auth_client(client)  # register writes an audit row
    before = _count_rows(client, action="audit_patrol")

    total, broken, fired = client.portal.call(ap.patrol_once)
    assert broken == -1 and fired is False and total > 0
    assert _count_rows(client, action="audit_patrol") == before + 1
    ap.reset_state()


def test_patrol_broken_alerts_once_then_recovers(client):
    ap.reset_state()
    _auth_client(client)
    pick_victim, tamper, restore = _tamper_helpers(client)

    try:
        base = len(_chain_alerts(client))  # prior alerts from other tests
        base_rule = _count_rows(client, source="alert_rule")
        pick_victim()
        tamper()

        # first patrol after tamper → critical alert fired
        total, broken, fired = client.portal.call(ap.patrol_once)
        assert broken >= 0 and fired is True
        assert _count_rows(client, source="alert_rule") == base_rule
        alerts = _chain_alerts(client)
        assert len(alerts) - base == 1 and alerts[-1]["level"] == "critical"

        # still broken → suppressed (no second alert)
        _, _, fired2 = client.portal.call(ap.patrol_once)
        assert fired2 is False
        assert len(_chain_alerts(client)) - base == 1

        # repaired → recovery info alert
        restore()
        _, broken3, fired3 = client.portal.call(ap.patrol_once)
        assert broken3 == -1 and fired3 is True
        alerts = _chain_alerts(client)
        assert len(alerts) - base == 2 and alerts[-1]["level"] == "info"
    finally:
        restore()
        ap.reset_state()


def test_manual_verify_endpoint_still_works(client):
    ap.reset_state()
    headers, uid = _auth_client(client)
    _promote_admin(client, uid)

    r = client.post("/api/v1/audit/verify", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["broken_index"] == -1
    ap.reset_state()
