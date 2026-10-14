"""Tests for ``/api/v1/accounts/*`` (M1 binding flow).

Covers: auth, probe success/400/503, bind (no password in response,
duplicate 409), list, sync refresh, delete + ownership 404.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

API = "/api/v1/accounts"

MT5_FIELDS = {
    "id", "login", "server", "name", "account_type", "leverage", "currency",
    "balance", "credit", "equity", "margin", "margin_free", "margin_level",
    "is_connected", "is_trade_allowed", "trade_frozen", "live_allowed",
    "algo_trading_enabled",
    "gateway_node_id", "has_credentials", "last_sync_at",
    "created_at", "updated_at",
}


class StubGateway:
    """Gateway stub covering login/logout (accounts flow)."""

    def __init__(self) -> None:
        self.balance = 10000.0
        self.fail_login: str | None = None  # detail message → 400
        self.unavailable = False

    async def login(self, login: int, password: str, server: str) -> dict:
        if self.unavailable:
            from app.services.gateway_client import GatewayUnavailable

            raise GatewayUnavailable(
                "行情网关不可达：Connection refused。"
                "请在「行情 · 数据管理」页确认网关服务已启动"
            )
        if self.fail_login:
            from app.services.gateway_client import GatewayLoginError

            raise GatewayLoginError(self.fail_login)
        return {
            "login": login,
            "server": server,
            "name": "Test Account",
            "type": "demo",
            "leverage": 100,
            "currency": "USD",
            "balance": self.balance,
            "credit": 0.0,
            "equity": self.balance,
            "margin": 120.0,
            "margin_free": self.balance - 120.0,
            "margin_level": 0.0,
            "trade_allowed": True,
        }

    async def aclose(self) -> None:  # pragma: no cover - compatibility
        pass


@pytest.fixture()
def stub() -> StubGateway:
    from app.main import app
    from app.services.gateway_client import get_gateway_client

    stub = StubGateway()
    app.dependency_overrides[get_gateway_client] = lambda: stub
    yield stub
    app.dependency_overrides.pop(get_gateway_client, None)


@pytest.fixture()
def token(client: TestClient) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"acct-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"acc{uuid.uuid4().hex[:8]}",
            "password": "Str0ngPass!x",
            "full_name": "账户测试",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _creds(login: int) -> dict:
    return {"login": login, "password": "broker-pass", "server": "MetaQuotes-Demo"}


def test_accounts_require_token(client: TestClient, stub: StubGateway) -> None:
    assert client.get(API).status_code == 401
    assert client.post(f"{API}/test", json=_creds(1)).status_code == 401


def test_probe_success_and_errors(
    client: TestClient, stub: StubGateway, token: str
) -> None:
    # success
    r = client.post(f"{API}/test", json=_creds(555001), headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["login"] == 555001
    assert body["account_type"] == "demo"
    assert body["balance"] == 10000.0

    # 登录失败 → 400 中文
    stub.fail_login = "MT5 登录失败：(10006, 'Invalid account')"
    r = client.post(f"{API}/test", json=_creds(555002), headers=_auth(token))
    assert r.status_code == 400
    assert "MT5 登录失败" in r.json()["detail"]
    stub.fail_login = None

    # 网关不可达 → 503 中文
    stub.unavailable = True
    r = client.post(f"{API}/test", json=_creds(555003), headers=_auth(token))
    assert r.status_code == 503
    assert "行情网关不可达" in r.json()["detail"]
    stub.unavailable = False


def test_bind_list_sync_unbind(
    client: TestClient, stub: StubGateway, token: str
) -> None:
    # -- 绑定：201，响应结构性无密码 ------------------------------------------
    r = client.post(
        f"{API}", json=_creds(610001), headers=_auth(token)
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert set(created) == MT5_FIELDS
    assert "password" not in created and "password_encrypted" not in created
    assert created["is_connected"] is True
    account_id = created["id"]

    # -- 重复绑定 → 409 --------------------------------------------------------
    r = client.post(f"{API}", json=_creds(610001), headers=_auth(token))
    assert r.status_code == 409
    assert "已被某个平台账号绑定" in r.json()["detail"]

    # -- 列表：无密码字段 ------------------------------------------------------
    r = client.get(API, headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert all(set(item) == MT5_FIELDS for item in body["items"])

    # -- 同步：余额刷新 + last_sync_at 更新 ------------------------------------
    stub.balance = 12345.67
    r = client.post(f"{API}/{account_id}/sync", headers=_auth(token))
    assert r.status_code == 200, r.text
    synced = r.json()
    assert synced["balance"] == 12345.67
    assert synced["equity"] == 12345.67
    assert synced["last_sync_at"] != created["last_sync_at"]

    # -- 解绑：204 → 列表为空 → 404 -------------------------------------------
    r = client.delete(f"{API}/{account_id}", headers=_auth(token))
    assert r.status_code == 204
    r = client.post(f"{API}/{account_id}/sync", headers=_auth(token))
    assert r.status_code == 404


def test_ownership_enforced(client: TestClient, stub: StubGateway, token: str) -> None:
    r = client.post(f"{API}", json=_creds(620001), headers=_auth(token))
    assert r.status_code == 201, r.text
    account_id = r.json()["id"]

    # 第二个用户
    r2 = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"other-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"oth{uuid.uuid4().hex[:8]}",
            "password": "Str0ngPass!x",
            "full_name": "其他用户",
        },
    )
    assert r2.status_code == 201
    other_token = r2.json()["access_token"]

    # 他人视角：404（不泄漏存在性），列表不含该账户
    r = client.delete(f"{API}/{account_id}", headers=_auth(other_token))
    assert r.status_code == 404
    r = client.get(API, headers=_auth(other_token))
    assert r.json()["total"] == 0
