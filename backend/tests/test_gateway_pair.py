"""Gateway pairing endpoint tests (M17 配对码).

Flow: admin mints a one-shot code (+ QR SVG) → gateway exchanges it for the
API key and its node row is upserted → the code cannot be reused.  Covers
admin gating, bad codes, rate limiting, and the audit trail.
"""

from __future__ import annotations

import uuid

from app.services import pair_store

API = "/api/v1/gateway"


def _reset_pair_state() -> None:
    """Clear one-shot codes AND the per-IP attempt window (test isolation)."""
    pair_store.reset()
    pair_store.reset_attempts()


def _register(client, *, role: str = "viewer") -> dict:
    email = f"gp-{uuid.uuid4().hex[:10]}@test.com"
    r = client.post(
        "/api/v1/auth/register",
        json={"email": email, "username": f"gp{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "配对测试"},
    )
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    if role == "admin":
        from jose import jwt
        from sqlalchemy import update

        from app.config import settings
        from app.engine.session import AsyncSessionLocal
        from app.models import User

        uid = jwt.decode(token, settings.JWT_SECRET_KEY,
                         algorithms=[settings.JWT_ALGORITHM])["sub"]

        async def _up():
            async with AsyncSessionLocal() as db:
                await db.execute(update(User).where(User.id == uuid.UUID(uid))
                                 .values(role="admin"))
                await db.commit()
        client.portal.call(_up)
    return {"Authorization": f"Bearer {token}"}


def test_pair_flow_end_to_end(client):
    _reset_pair_state()
    admin = _register(client, role="admin")

    r = client.post(f"{API}/pair-codes", headers=admin)
    assert r.status_code == 201, r.text
    body = r.json()
    code = body["code"]
    assert len(code.replace("-", "")) == 8 and "-" in code
    assert body["server_url"].startswith("http")
    assert body["qr_svg"], "QR SVG payload must not be empty"
    import base64

    decoded = base64.b64decode(body["qr_svg"]).decode("utf-8", "replace")
    assert "<svg" in decoded.lower()

    # gateway exchanges the code → node upsert + shared key
    host, port = "10.1.2.3", 8091
    r2 = client.post(f"{API}/pair", json={
        "code": code, "host": host, "port": port, "name": "测试网关",
    })
    assert r2.status_code == 200, r2.text
    paired = r2.json()
    assert paired["host"] == host and paired["port"] == port
    assert len(paired["api_key"]) >= 8

    # node row exists and is listed
    nodes = client.get(f"{API}/nodes", headers=admin).json()["items"]
    assert any(n["host"] == host and n["port"] == port for n in nodes)

    # one-shot: same code rejected
    r3 = client.post(f"{API}/pair", json={
        "code": code, "host": host, "port": port, "name": "测试网关",
    })
    assert r3.status_code == 404
    _reset_pair_state()


def test_pair_rejects_bad_code_and_gates_admin(client):
    _reset_pair_state()
    viewer = _register(client)

    # non-admin cannot mint codes
    assert client.post(f"{API}/pair-codes", headers=viewer).status_code == 403

    # unknown / malformed codes → 404 (no enumeration)
    assert client.post(f"{API}/pair", json={
        "code": "ZZZZ-ZZZZ", "host": "h", "port": 1,
    }).status_code == 404
    assert client.post(f"{API}/pair", json={
        "code": "1234-5678", "host": "h", "port": 1,
    }).status_code == 404
    _reset_pair_state()


def test_pair_rate_limit(client):
    _reset_pair_state()
    hit_429 = False
    for _ in range(12):
        r = client.post(f"{API}/pair", json={
            "code": "AAAA-AAAA", "host": "h", "port": 1,
        })
        if r.status_code == 429:
            hit_429 = True
            break
    assert hit_429
    _reset_pair_state()


def test_pair_repair_display_forms(client):
    """Space / lowercase variants of a code are accepted."""
    _reset_pair_state()
    admin = _register(client, role="admin")
    code = client.post(f"{API}/pair-codes", headers=admin).json()["code"]
    raw = code.replace("-", "").lower()

    r = client.post(f"{API}/pair", json={
        "code": raw, "host": "10.9.9.9", "port": 8099, "name": "变体",
    })
    assert r.status_code == 200, r.text
    _reset_pair_state()
