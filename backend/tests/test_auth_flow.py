"""End-to-end tests for the authentication flow (``/api/v1/auth/*``).

Covers: register (first user = admin), conflict detection, login with
uniform 401, ``GET /me``, refresh rotation with replay rejection, and logout
revocation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

API = "/api/v1/auth"

USER_FIELDS = {
    "id",
    "email",
    "username",
    "full_name",
    "role",
    "is_active",
    "totp_enabled",
    "created_at",
    "updated_at",
}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_full_auth_flow(client: TestClient) -> None:
    # 本会话可能已有其它测试先注册了用户；本用例验证「首个用户=admin」规则，
    # 因此先清空 users（会话/MT5 绑定分别级联删除/置空，不影响其它用例）。
    import asyncio

    import asyncpg

    from tests.conftest import TEST_DATABASE_URL

    async def _reset_users() -> None:
        conn = await asyncpg.connect(TEST_DATABASE_URL.replace("+asyncpg", ""))
        try:
            await conn.execute("DELETE FROM users")
        finally:
            await conn.close()

    asyncio.run(_reset_users())

    # -- 1. Register: first user becomes admin --------------------------------
    r = client.post(
        f"{API}/register",
        json={
            "email": "admin@example.com",
            "username": "admin",
            "password": "Str0ngPass!x",
            "full_name": "管理员",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert set(body["user"]) == USER_FIELDS
    assert body["user"]["role"] == "admin"
    access = body["access_token"]

    # -- 2. Conflicts ----------------------------------------------------------
    r = client.post(
        f"{API}/register",
        json={
            "email": "admin@example.com",
            "username": "other1",
            "password": "Str0ngPass!x",
            "full_name": "Other",
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "Email already registered"

    r = client.post(
        f"{API}/register",
        json={
            "email": "other@example.com",
            "username": "admin",
            "password": "Str0ngPass!x",
            "full_name": "Other",
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "Username already taken"

    # -- 3. GET /me ------------------------------------------------------------
    r = client.get(f"{API}/me", headers=_auth(access))
    assert r.status_code == 200
    me = r.json()
    assert set(me) == USER_FIELDS
    assert me["email"] == "admin@example.com"
    uuid.UUID(me["id"])  # well-formed uuid

    # -- 4. Login: uniform 401, no user enumeration ----------------------------
    r = client.post(f"{API}/login", json={"email": "admin@example.com", "password": "wrong-pass-1"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid email or password"

    r = client.post(f"{API}/login", json={"email": "nobody@example.com", "password": "whatever-1"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid email or password"

    r = client.post(
        f"{API}/login",
        json={"email": "admin@example.com", "password": "Str0ngPass!x"},
    )
    assert r.status_code == 200
    access2, refresh2 = r.json()["access_token"], r.json()["refresh_token"]
    assert access2 and refresh2

    # -- 5. Refresh rotation: old token must be rejected after rotation --------
    r = client.post(f"{API}/refresh", json={"refresh_token": refresh2})
    assert r.status_code == 200
    rotated = r.json()["refresh_token"]
    assert rotated != refresh2

    r = client.post(f"{API}/refresh", json={"refresh_token": refresh2})
    assert r.status_code == 401  # replay of the rotated-away token
    assert r.json()["detail"] == "Invalid or expired session"

    # -- 6. Logout revokes the session ----------------------------------------
    r = client.post(f"{API}/logout", json={"refresh_token": rotated})
    assert r.status_code == 200

    r = client.post(f"{API}/refresh", json={"refresh_token": rotated})
    assert r.status_code == 401

    # logout is idempotent for unknown/revoked tokens
    r = client.post(f"{API}/logout", json={"refresh_token": rotated})
    assert r.status_code == 200

    # -- 7. Re-login after logout ---------------------------------------------
    r = client.post(
        f"{API}/login",
        json={"email": "admin@example.com", "password": "Str0ngPass!x"},
    )
    assert r.status_code == 200
    r = client.get(f"{API}/me", headers=_auth(r.json()["access_token"]))
    assert r.status_code == 200


def test_me_requires_token(client: TestClient) -> None:
    assert client.get(f"{API}/me").status_code == 401
    assert client.get(f"{API}/me", headers=_auth("not-a-jwt")).status_code == 401


def test_refresh_rejects_bogus_token(client: TestClient) -> None:
    r = client.post(f"{API}/refresh", json={"refresh_token": "totally-made-up"})
    assert r.status_code == 401


def test_register_validation(client: TestClient) -> None:
    # weak password → 422
    r = client.post(
        f"{API}/register",
        json={
            "email": "weak@example.com",
            "username": "weak1",
            "password": "short",
            "full_name": "Weak",
        },
    )
    assert r.status_code == 422

    # bad username characters → 422
    r = client.post(
        f"{API}/register",
        json={
            "email": "bad-user@example.com",
            "username": "bad user!",
            "password": "Str0ngPass!x",
            "full_name": "Bad",
        },
    )
    assert r.status_code == 422


def test_session_row_records_client_meta(client: TestClient) -> None:
    """The issued session stores UA / IP for audit purposes."""
    r = client.post(
        f"{API}/register",
        json={
            "email": "meta@example.com",
            "username": "metauser",
            "password": "Str0ngPass!x",
            "full_name": "Meta",
        },
        headers={"User-Agent": "pytest-agent/1.0"},
    )
    assert r.status_code == 201

    import asyncio

    import asyncpg

    from tests.conftest import TEST_DATABASE_URL

    async def _fetch():
        # Raw asyncpg (own connection, own loop) — avoids reusing the
        # TestClient portal's loop-bound SQLAlchemy pool connections.
        conn = await asyncpg.connect(TEST_DATABASE_URL.replace("+asyncpg", ""))
        try:
            return await conn.fetch(
                "SELECT ip_address, revoked_at, expires_at FROM user_sessions"
                " WHERE user_agent = 'pytest-agent/1.0'"
            )
        finally:
            await conn.close()

    rows = asyncio.run(_fetch())
    assert len(rows) == 1
    assert rows[0]["ip_address"] is not None
    assert rows[0]["revoked_at"] is None
    assert rows[0]["expires_at"] > datetime.now(timezone.utc)
