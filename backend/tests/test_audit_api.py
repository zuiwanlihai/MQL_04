"""Audit query / verify / export API tests (M7 Phase A)."""

from __future__ import annotations

import uuid

import pytest

API = "/api/v1/audit"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(client) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"au-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"au{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "审计测试"},
    )
    assert r.status_code == 201
    return r.json()["access_token"]


def _me_id(client, token: str) -> str:
    return client.get("/api/v1/auth/me", headers=_auth(token)).json()["id"]


def _promote(client, user_id: str, role: str = "admin") -> None:
    async def _set():
        from app.engine.session import AsyncSessionLocal
        from app.models import User

        async with AsyncSessionLocal() as db:
            u = await db.get(User, uuid.UUID(user_id))
            u.role = role
            # the session's very first user is auto-superuser → clear it so
            # the role gate is actually exercised
            u.is_superuser = role == "admin"
            db.add(u)
            await db.commit()
    client.portal.call(_set)


def _seed_rows(client, action: str, target_id: str, n: int = 2) -> None:
    async def _run():
        from app.services.audit import AuditActor, record

        for i in range(n):
            await record(action,
                         actor=AuditActor(actor_type="user", actor_label="seed"),
                         target_type="live_run", target_id=f"{target_id}-{i}",
                         params={"i": i})
    client.portal.call(_run)


def test_audit_list_requires_admin(client, token):
    # the very first registered user in the session is auto-admin → demote
    _promote(client, _me_id(client, token), role="trader")
    r = client.get(API, headers=_auth(token))
    assert r.status_code == 403


def test_audit_list_and_filters(client, token):
    _promote(client, _me_id(client, token))
    action = f"live_deploy_{uuid.uuid4().hex[:6]}"
    target = uuid.uuid4().hex
    _seed_rows(client, action, target)

    r = client.get(API, headers=_auth(token), params={"action": action})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert all(i["action"] == action for i in body["items"])
    assert {i["target_id"] for i in body["items"]} == {f"{target}-0", f"{target}-1"}

    r2 = client.get(API, headers=_auth(token),
                    params={"action": action, "target_id": f"{target}-0"})
    assert r2.json()["total"] == 1

    r3 = client.get(API, headers=_auth(token), params={"result": "bogus"})
    assert r3.status_code == 422


def test_audit_verify_intact_and_tampered(client, token):
    _promote(client, _me_id(client, token))
    _seed_rows(client, "live_stop", uuid.uuid4().hex)

    r = client.post(f"{API}/verify", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["broken_index"] == -1
    assert body["total"] >= 2

    # tamper the newest row → verify reports the break + emits critical alert;
    # restore afterwards so the session-wide chain stays intact for later tests
    async def _tamper():
        from sqlalchemy import select, update

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog

        async with AsyncSessionLocal() as db:
            last = (await db.execute(
                select(AuditLog).order_by(AuditLog.created_at.desc(),
                                          AuditLog.id.desc()).limit(1)
            )).scalars().first()
            original = dict(last.params)
            await db.execute(
                update(AuditLog).where(AuditLog.id == last.id)
                .values(params={"tampered": True})
            )
            await db.commit()
        return str(last.id), original

    tampered_id, original_params = client.portal.call(_tamper)

    r2 = client.post(f"{API}/verify", headers=_auth(token))
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["ok"] is False
    assert 0 <= body2["broken_index"] < body2["total"]

    async def _restore():
        from sqlalchemy import update

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog

        async with AsyncSessionLocal() as db:
            await db.execute(
                update(AuditLog).where(AuditLog.id == uuid.UUID(tampered_id))
                .values(params=original_params)
            )
            await db.commit()
    client.portal.call(_restore)

    r3 = client.post(f"{API}/verify", headers=_auth(token))
    assert r3.json()["ok"] is True

    from app.models import AlertEvent

    async def _alert_count():
        from sqlalchemy import func, select

        from app.engine.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            return (await db.execute(
                select(func.count()).select_from(AlertEvent)
                .where(AlertEvent.source == "audit_chain")
            )).scalar_one()
    assert client.portal.call(_alert_count) >= 1


def test_audit_export_csv_records_audit(client, token):
    _promote(client, _me_id(client, token))
    action = f"live_reset_{uuid.uuid4().hex[:6]}"
    _seed_rows(client, action, uuid.uuid4().hex)

    r = client.get(f"{API}/export", headers=_auth(token),
                   params={"format": "csv", "action": action})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    text = r.text
    assert text.startswith("\ufeff")
    assert "action" in text.splitlines()[0]
    assert sum(1 for line in text.splitlines() if action in line) >= 2

    rj = client.get(f"{API}/export", headers=_auth(token),
                    params={"format": "json", "action": action})
    assert rj.status_code == 200
    assert len(rj.json()) >= 2

    # the export itself must be audited
    async def _export_rows():
        from sqlalchemy import func, select

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog

        async with AsyncSessionLocal() as db:
            return (await db.execute(
                select(func.count()).select_from(AuditLog)
                .where(AuditLog.action == "audit_export")
            )).scalar_one()
    assert client.portal.call(_export_rows) >= 2
