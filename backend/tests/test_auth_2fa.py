"""TOTP 2FA flow tests (M9).

Covers the invariant (no-2FA accounts keep the exact pre-M9 login shape),
setup→enable→two-step login, recovery-code single use, disable re-confirm,
pre-auth token isolation, and the audit trail.
"""

from __future__ import annotations

import uuid

API = "/api/v1/auth"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, email: str | None = None) -> tuple[str, str]:
    email = email or f"2fa-{uuid.uuid4().hex[:10]}@test.com"
    r = client.post(
        f"{API}/register",
        json={"email": email, "username": f"u{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "2FA测试"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"], email


def _totp_now(secret: str) -> str:
    import pyotp

    return pyotp.TOTP(secret).now()


def _enable(client, token: str) -> tuple[str, list[str]]:
    setup = client.post(f"{API}/2fa/setup", headers=_auth(token))
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    assert setup.json()["qr_svg_base64"].startswith("data:image/svg+xml;base64,")
    en = client.post(f"{API}/2fa/enable", json={"code": _totp_now(secret)},
                     headers=_auth(token))
    assert en.status_code == 200, en.text
    return secret, en.json()["recovery_codes"]


def test_no_2fa_login_shape_unchanged(client):
    """Invariant: accounts without 2FA get the exact pre-M9 SessionResponse."""
    token, email = _register(client)
    r = client.post(f"{API}/login", json={"email": email, "password": "Passw0rd!"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"access_token", "refresh_token", "token_type", "expires_in", "user"}
    assert body["user"]["totp_enabled"] is False
    # refresh keeps working for the session issued in login
    rr = client.post(f"{API}/refresh", json={"refresh_token": body["refresh_token"]})
    assert rr.status_code == 200


def test_setup_requires_valid_code_and_enable_flips_status(client):
    token, _ = _register(client)
    setup = client.post(f"{API}/2fa/setup", headers=_auth(token))
    assert setup.status_code == 200

    bad = client.post(f"{API}/2fa/enable", json={"code": "000000"}, headers=_auth(token))
    assert bad.status_code == 422

    st = client.get(f"{API}/2fa/status", headers=_auth(token))
    assert st.json() == {"enabled": False, "recovery_codes_remaining": 0}

    secret, codes = _enable(client, token)
    assert len(codes) == 10
    st = client.get(f"{API}/2fa/status", headers=_auth(token))
    assert st.json() == {"enabled": True, "recovery_codes_remaining": 10}

    # already enabled → setup rejected
    assert client.post(f"{API}/2fa/setup", headers=_auth(token)).status_code == 409


def test_two_step_login_with_totp(client):
    token, email = _register(client)
    secret, _ = _enable(client, token)

    r = client.post(f"{API}/login", json={"email": email, "password": "Passw0rd!"})
    assert r.status_code == 200
    challenge = r.json()
    assert challenge["requires_2fa"] is True
    assert "access_token" not in challenge
    pre = challenge["pre_auth_token"]

    # wrong code → 401
    bad = client.post(f"{API}/login/2fa",
                      json={"pre_auth_token": pre, "totp_code": "123456"})
    assert bad.status_code == 401

    ok = client.post(f"{API}/login/2fa",
                     json={"pre_auth_token": pre, "totp_code": _totp_now(secret)})
    assert ok.status_code == 200, ok.text
    session = ok.json()
    assert "access_token" in session
    me = client.get(f"{API}/me", headers=_auth(session["access_token"]))
    assert me.json()["totp_enabled"] is True


def test_pre_auth_token_cannot_access_business_api(client):
    token, email = _register(client)
    _enable(client, token)
    r = client.post(f"{API}/login", json={"email": email, "password": "Passw0rd!"})
    pre = r.json()["pre_auth_token"]

    # pre-auth token is NOT an access token
    biz = client.get("/api/v1/live/meta/status", headers=_auth(pre))
    assert biz.status_code in (401, 403)
    # garbage token rejected
    bad = client.post(f"{API}/login/2fa",
                      json={"pre_auth_token": "not.a.jwt", "totp_code": "123456"})
    assert bad.status_code == 401


def test_recovery_code_single_use(client):
    token, email = _register(client)
    _, codes = _enable(client, token)
    r = client.post(f"{API}/login", json={"email": email, "password": "Passw0rd!"})
    pre = r.json()["pre_auth_token"]

    ok = client.post(f"{API}/login/2fa",
                     json={"pre_auth_token": pre, "totp_code": codes[0]})
    assert ok.status_code == 200

    st = client.get(f"{API}/2fa/status", headers=_auth(ok.json()["access_token"]))
    assert st.json()["recovery_codes_remaining"] == 9

    # consumed code no longer works
    r2 = client.post(f"{API}/login", json={"email": email, "password": "Passw0rd!"})
    pre2 = r2.json()["pre_auth_token"]
    reuse = client.post(f"{API}/login/2fa",
                        json={"pre_auth_token": pre2, "totp_code": codes[0]})
    assert reuse.status_code == 401


def test_disable_requires_password_reconfirm(client):
    token, email = _register(client)
    _enable(client, token)

    bad = client.post(f"{API}/2fa/disable", json={"password": "WrongPass!1"},
                      headers=_auth(token))
    assert bad.status_code == 401

    ok = client.post(f"{API}/2fa/disable", json={"password": "Passw0rd!"},
                     headers=_auth(token))
    assert ok.status_code == 204

    st = client.get(f"{API}/2fa/status", headers=_auth(token))
    assert st.json() == {"enabled": False, "recovery_codes_remaining": 0}

    # back to one-step login
    r = client.post(f"{API}/login", json={"email": email, "password": "Passw0rd!"})
    assert "access_token" in r.json()


def test_2fa_actions_are_audited(client):
    token, email = _register(client)
    _enable(client, token)
    client.post(f"{API}/login", json={"email": email, "password": "Passw0rd!"})

    async def _read():
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(AuditLog.action).where(AuditLog.action.in_(
                    ("login", "login_2fa", "2fa_setup", "2fa_enable", "logout")))
            )).scalars().all()
        return set(rows)

    actions = client.portal.call(_read)
    assert {"login", "login_2fa", "2fa_setup", "2fa_enable"} <= actions


def test_totp_secret_stored_encrypted(client):
    token, _ = _register(client)
    secret, _ = _enable(client, token)
    me = client.get(f"{API}/me", headers=_auth(token)).json()

    async def _read():
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import User

        async with AsyncSessionLocal() as db:
            return (await db.execute(
                select(User.totp_secret).where(User.id == uuid.UUID(me["id"]))
            )).scalar_one()

    stored = client.portal.call(_read)
    assert stored is not None
    assert stored.startswith("v1:")  # Fernet ciphertext
    assert secret not in stored  # plaintext never persisted
