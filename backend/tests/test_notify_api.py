"""Notification channel CRUD + dispatch tests (M10 Phase A).

Network senders are monkeypatched (no real SMTP / Telegram); the tests
verify masking, validation, min_level gating, audit trail, and the
non-blocking guarantee that a failing send never breaks emit().
"""

from __future__ import annotations

import uuid

API = "/api/v1/notify"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _token(client) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"nt-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"nt{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "渠道测试"},
    )
    assert r.status_code == 201
    return r.json()["access_token"]


def _create(client, token, kind="email", target="trader@example.com", min_level="warn"):
    return client.post(f"{API}/channels",
                       json={"kind": kind, "target": target, "min_level": min_level},
                       headers=_auth(token))


def test_channel_crud_and_masking(client):
    token = _token(client)
    r = _create(client, token, "telegram", "123456:9876543210")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["target_masked"] == "***:3210"  # bot token never echoed
    assert "123456" not in body["target_masked"]
    cid = body["id"]

    lst = client.get(f"{API}/channels", headers=_auth(token)).json()
    assert len(lst) == 1 and lst[0]["kind"] == "telegram"

    upd = client.patch(f"{API}/channels/{cid}", json={"min_level": "critical", "enabled": False},
                       headers=_auth(token))
    assert upd.status_code == 200
    assert upd.json()["min_level"] == "critical" and upd.json()["enabled"] is False

    assert client.delete(f"{API}/channels/{cid}", headers=_auth(token)).status_code == 204
    assert client.get(f"{API}/channels", headers=_auth(token)).json() == []


def test_channel_validation_and_duplicates(client):
    token = _token(client)
    assert _create(client, token, "email", "not-an-email").status_code == 422
    assert _create(client, token, "telegram", "no-colon").status_code == 422
    assert _create(client, token, "dingtalk", "ftp://x").status_code == 422

    ok = _create(client, token, "email", "dup@example.com")
    assert ok.status_code == 201
    dup = _create(client, token, "email", "dup@example.com")
    assert dup.status_code == 409


def test_channel_isolation_between_users(client):
    t1, t2 = _token(client), _token(client)
    cid = _create(client, t1, "email", "a@example.com").json()["id"]
    assert client.get(f"{API}/channels", headers=_auth(t2)).json() == []
    assert client.patch(f"{API}/channels/{cid}", json={"enabled": False},
                        headers=_auth(t2)).status_code == 404
    assert client.delete(f"{API}/channels/{cid}", headers=_auth(t2)).status_code == 404


def test_level_admits_logic():
    from app.services.notify.dispatcher import level_admits

    assert level_admits("warn", "critical") is True
    assert level_admits("warn", "warn") is True
    assert level_admits("warn", "info") is False
    assert level_admits("info", "info") is True
    assert level_admits("critical", "error") is False


def test_test_channel_uses_sender_and_audits(client, monkeypatch):
    token = _token(client)
    cid = _create(client, token, "email", "t@example.com").json()["id"]

    import app.api.v1.endpoints.notify as notify_mod

    calls = []

    async def fake_send_email(to, subject, body):
        calls.append(to)
        return True, "已发送"

    monkeypatch.setattr(notify_mod, "send_email", fake_send_email)

    r = client.post(f"{API}/channels/{cid}/test", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"ok": True, "detail": "已发送"}
    assert calls == ["t@example.com"]

    # verified_at flipped
    got = client.get(f"{API}/channels", headers=_auth(token)).json()[0]
    assert got["verified_at"] is not None

    async def fake_fail(to, subject, body):
        return False, "SMTP 未配置"

    monkeypatch.setattr(notify_mod, "send_email", fake_fail)
    r2 = client.post(f"{API}/channels/{cid}/test", headers=_auth(token))
    assert r2.json()["ok"] is False

    actions = set()

    async def _read():
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(AuditLog.action).where(AuditLog.action.in_(
                    ("channel_change", "channel_test")))
            )).scalars().all()
        return set(rows)

    actions = client.portal.call(_read)
    assert {"channel_change", "channel_test"} <= actions


def test_deliver_survives_sender_crash(client, monkeypatch):
    """A crashing sender must not propagate — emit() sits on the kill-switch path."""
    import app.services.alert as alert_mod
    import app.services.notify.dispatcher as disp_mod

    monkeypatch.setattr(alert_mod, "schedule_dispatch", lambda alert_id: None)
    token = _token(client)
    _create(client, token, "email", "crash@example.com")

    async def boom(*args, **kwargs):
        raise RuntimeError("sender exploded")

    async def fake_webhook(*args, **kwargs):
        return True, "已发送"

    monkeypatch.setattr(disp_mod, "send_email", boom)
    monkeypatch.setattr(disp_mod, "SENDERS",
                        {"telegram": fake_webhook, "dingtalk": fake_webhook,
                         "wecom": fake_webhook})

    async def _run():
        row = await alert_mod.emit("kill_switch", "熔断测试", "message", level="critical")
        await disp_mod._deliver(row.id)  # must not raise despite the crashing sender
        return row

    row = client.portal.call(_run)
    assert row.id is not None
    assert row.level == "critical"


def test_emit_writes_row_then_schedules(client, monkeypatch):
    """emit() must persist the in-site row BEFORE scheduling dispatch."""
    import app.services.alert as alert_mod

    seen: list = []

    def fake_schedule(alert_id):
        seen.append(alert_id)

    monkeypatch.setattr(alert_mod, "schedule_dispatch", fake_schedule)

    async def _emit():
        return await alert_mod.emit("reconcile", "对账告警", "detail", level="error")

    row = client.portal.call(_emit)
    assert seen == [row.id]

    async def _read():
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AlertEvent

        async with AsyncSessionLocal() as db:
            return (await db.execute(
                select(AlertEvent).where(AlertEvent.id == row.id)
            )).scalars().first()

    assert client.portal.call(_read) is not None
