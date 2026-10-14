"""Strategy CRUD / versions / templates / validate API tests (M3)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

API = "/api/v1/strategies"

STRATEGY_FIELDS = {
    "id", "name", "description", "language", "direction", "status",
    "current_version", "symbol_ids", "timeframe", "tags", "parameters",
    "created_by", "created_at", "updated_at",
}

MINIMAL = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_bar(ctx, bar):
    pass

def on_order(ctx, order):
    pass
"""


@pytest.fixture
def token(client: TestClient) -> str:
    email = f"strat-{uuid.uuid4().hex[:10]}@test.com"
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "username": f"strat{uuid.uuid4().hex[:10]}",
            "password": "Passw0rd!",
            "full_name": "策略测试",
        },
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create(client, token, name=None, **kw):
    payload = {"name": name or f"s-{uuid.uuid4().hex[:8]}", **kw}
    return client.post(API, json=payload, headers=_auth(token))


# ── CRUD ────────────────────────────────────────────────────────

def test_create_and_get_detail(client, token):
    resp = _create(client, token, description="测试策略", symbol_ids=["EURUSD"])
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == STRATEGY_FIELDS
    assert body["status"] == "draft"
    assert body["current_version"] is None

    detail = client.get(f"{API}/{body['id']}", headers=_auth(token))
    assert detail.status_code == 200
    assert "source_code" in detail.json()


def test_duplicate_name_409(client, token):
    name = f"dup-{uuid.uuid4().hex[:8]}"
    assert _create(client, token, name=name).status_code == 201
    assert _create(client, token, name=name).status_code == 409


def test_mql5_rejected(client, token):
    resp = _create(client, token, language="mql5")
    assert resp.status_code == 422
    assert "python" in resp.json()["detail"]


def test_list_pagination_and_exclude_source(client, token):
    for _ in range(3):
        assert _create(client, token).status_code == 201
    resp = client.get(f"{API}?page=1&page_size=2", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 3
    assert len(body["items"]) == 2
    assert body["total_pages"] >= 2
    assert all("source_code" not in item for item in body["items"])


def test_list_status_and_q_filter(client, token):
    a = _create(client, token, name=f"alpha-{uuid.uuid4().hex[:6]}").json()
    client.patch(f"{API}/{a['id']}", json={"status": "active"}, headers=_auth(token))
    resp = client.get(f"{API}?status=active", headers=_auth(token))
    assert all(i["status"] == "active" for i in resp.json()["items"])
    assert any(i["id"] == a["id"] for i in resp.json()["items"])

    q = client.get(f"{API}?q=alpha-{a['name'].split('alpha-')[1]}", headers=_auth(token))
    assert q.json()["total"] == 1


def test_patch_and_delete(client, token):
    s = _create(client, token).json()
    resp = client.patch(
        f"{API}/{s['id']}", json={"description": "新描述", "tags": ["t1"]},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.json()["description"] == "新描述"

    assert client.delete(f"{API}/{s['id']}", headers=_auth(token)).status_code == 204
    assert client.get(f"{API}/{s['id']}", headers=_auth(token)).status_code == 404


def test_ownership_isolated(client, token):
    s = _create(client, token).json()
    other = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"other-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"other{uuid.uuid4().hex[:6]}",
            "password": "Passw0rd!",
            "full_name": "他人",
        },
    ).json()["access_token"]
    assert client.get(f"{API}/{s['id']}", headers=_auth(other)).status_code == 404
    assert client.patch(
        f"{API}/{s['id']}", json={"description": "x"}, headers=_auth(other)
    ).status_code == 404
    assert client.delete(f"{API}/{s['id']}", headers=_auth(other)).status_code == 404


# ── Working copy ────────────────────────────────────────────────

def test_save_source_with_validation(client, token):
    s = _create(client, token).json()
    resp = client.put(
        f"{API}/{s['id']}/source",
        json={"source_code": MINIMAL, "base_updated_at": s["updated_at"]},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_code"] == MINIMAL
    assert body["validation"]["ok"] is True


def test_save_source_optimistic_lock_409(client, token):
    s = _create(client, token).json()
    stale = "2020-01-01T00:00:00Z"
    resp = client.put(
        f"{API}/{s['id']}/source",
        json={"source_code": MINIMAL, "base_updated_at": stale},
        headers=_auth(token),
    )
    assert resp.status_code == 409
    assert "刷新" in resp.json()["detail"]


def test_archived_cannot_edit_source(client, token):
    s = _create(client, token).json()
    client.patch(f"{API}/{s['id']}", json={"status": "archived"}, headers=_auth(token))
    resp = client.put(
        f"{API}/{s['id']}/source",
        json={"source_code": MINIMAL},
        headers=_auth(token),
    )
    assert resp.status_code == 409
    assert "归档" in resp.json()["detail"]


# ── Versions ────────────────────────────────────────────────────

def _save(client, token, sid, code):
    return client.put(
        f"{API}/{sid}/source", json={"source_code": code}, headers=_auth(token)
    )


def test_freeze_version_flow(client, token):
    s = _create(client, token).json()
    _save(client, token, s["id"], MINIMAL)

    v1 = client.post(
        f"{API}/{s['id']}/versions", json={"changelog": "首版"}, headers=_auth(token)
    )
    assert v1.status_code == 201
    assert v1.json()["version"] == 1

    # 无变化再冻结 → 409
    dup = client.post(f"{API}/{s['id']}/versions", json={}, headers=_auth(token))
    assert dup.status_code == 409
    assert "v1" in dup.json()["detail"]

    # 改代码后 v2
    _save(client, token, s["id"], MINIMAL + "\n# v2\n")
    v2 = client.post(f"{API}/{s['id']}/versions", json={}, headers=_auth(token))
    assert v2.status_code == 201
    assert v2.json()["version"] == 2

    detail = client.get(f"{API}/{s['id']}", headers=_auth(token)).json()
    assert detail["current_version"] == 2

    # 回滚 v1 → 产生 v3，代码为 v1
    rb = client.post(
        f"{API}/{s['id']}/versions/1/rollback", json={}, headers=_auth(token)
    )
    assert rb.status_code == 201
    assert rb.json()["version"] == 3
    assert rb.json()["source_code"] == MINIMAL

    # 回滚到当前版本 → 409
    again = client.post(
        f"{API}/{s['id']}/versions/3/rollback", json={}, headers=_auth(token)
    )
    assert again.status_code == 409

    # 回滚不存在的版本 → 404
    missing = client.post(
        f"{API}/{s['id']}/versions/99/rollback", json={}, headers=_auth(token)
    )
    assert missing.status_code == 404

    lst = client.get(f"{API}/{s['id']}/versions", headers=_auth(token))
    assert lst.json()["total"] == 3


def test_freeze_invalid_code_422_with_validation(client, token):
    s = _create(client, token).json()
    _save(client, token, s["id"], "import os\ndef on_init(ctx):\n    pass\n")
    resp = client.post(f"{API}/{s['id']}/versions", json={}, headers=_auth(token))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["ok"] is False
    assert any(i["code"] == "I001" for i in detail["issues"])


# ── Validate endpoint ───────────────────────────────────────────

def test_validate_always_200(client, token):
    resp = client.post(
        f"{API}/validate", json={"source_code": "import os\n"}, headers=_auth(token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["issues"][0]["code"] == "I001"


# ── Templates ───────────────────────────────────────────────────

def test_templates_seeded(client, token):
    resp = client.get(f"{API}/templates", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 6  # boll_macd_ma_v4 + 5 个 SDK v1 模板
    assert all("source_code" not in item for item in body["items"])

    first = body["items"][0]
    detail = client.get(f"{API}/templates/{first['id']}", headers=_auth(token))
    assert detail.status_code == 200
    assert "def on_init" in detail.json()["source_code"]


def test_create_from_template(client, token):
    tpl = client.get(f"{API}/templates", headers=_auth(token)).json()["items"][0]
    s = client.post(
        API, json={"name": f"tpl-{uuid.uuid4().hex[:6]}", "template_id": tpl["id"]},
        headers=_auth(token),
    )
    assert s.status_code == 201
    detail = client.get(f"{API}/{s.json()['id']}", headers=_auth(token)).json()
    assert "def on_init" in detail["source_code"]


def test_template_not_found(client, token):
    resp = client.get(f"{API}/templates/{uuid.uuid4()}", headers=_auth(token))
    assert resp.status_code == 404
