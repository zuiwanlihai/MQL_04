"""Account activation + active-gateway probe endpoints (总览账户登录 al-2).

POST /accounts/{id}/activate — gateway re-login with the stored encrypted
credentials (probing THAT node's URL), mirror-refresh the row and audit-log
``account_activate``.

GET  /accounts/active        — per-node currently-logged-in MT5 account
(live probe; unreachable node → account=None, never persisted).

The activate path always goes through ``GatewayClient(node_url)`` inside
``_probe`` (gateway_node_id is non-null), which bypasses
``dependency_overrides`` — hence the class-level ``RecordingStub`` patch
(same technique as ``test_account_bind_node.py``).
"""

from __future__ import annotations

import uuid

import pytest

API = "/api/v1/accounts"

RAW_LOGIN = {
    "login": 888001,
    "server": "NodeBroker-Demo",
    "name": "Activated Account",
    "type": "real",
    "leverage": 100,
    "currency": "USD",
    "balance": 7777.0,
    "credit": 0.0,
    "equity": 7777.0,
    "margin": 0.0,
    "margin_free": 7777.0,
    "margin_level": 0.0,
    "trade_allowed": True,
}


class RecordingStub:
    """GatewayClient replacement recording logins / controlling health()."""

    instances: list["RecordingStub"] = []
    fail_login: str | None = None
    unavailable: bool = False
    fail_health: bool = False
    health_account: dict | None = dict(RAW_LOGIN)

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url
        self.logins: list[tuple] = []
        RecordingStub.instances.append(self)

    @classmethod
    def for_registered_node(cls, base_url: str | None = None) -> RecordingStub:
        # 桩不区分凭据来源（注册表节点 / 平台默认地址）；「密钥该不该带」由
        # tests/test_gateway_key_scope.py 对着真实客户端验。
        return cls(base_url)

    async def login(self, login: int, password: str, server: str) -> dict:
        self.logins.append((login, password, server))
        if RecordingStub.unavailable:
            from app.services.gateway_client import GatewayUnavailable

            raise GatewayUnavailable("行情网关不可达：stub down")
        if RecordingStub.fail_login:
            from app.services.gateway_client import GatewayLoginError

            raise GatewayLoginError(RecordingStub.fail_login)
        return {**RAW_LOGIN, "login": login, "server": server}

    async def health(self) -> dict:
        if RecordingStub.fail_health:
            from app.services.gateway_client import GatewayUnavailable

            raise GatewayUnavailable("行情网关不可达：stub down")
        if RecordingStub.health_account is None:
            return {}
        return {"account": dict(RecordingStub.health_account)}

    async def aclose(self) -> None:
        pass


@pytest.fixture
def stub(monkeypatch):
    import app.api.v1.endpoints.accounts as acc_mod
    from app.main import app
    from app.services.gateway_client import get_gateway_client

    RecordingStub.instances = []
    RecordingStub.fail_login = None
    RecordingStub.unavailable = False
    RecordingStub.fail_health = False
    RecordingStub.health_account = dict(RAW_LOGIN)
    monkeypatch.setattr(acc_mod, "GatewayClient", RecordingStub)

    class _Default:
        """Injected default client — activate must NOT use it (node path)."""

        async def login(self, login, password, server) -> dict:
            raise AssertionError("activate 应走节点 URL 而非默认 client")

        async def aclose(self) -> None:
            pass

    app.dependency_overrides[get_gateway_client] = lambda: _Default()
    yield RecordingStub
    app.dependency_overrides.pop(get_gateway_client, None)


@pytest.fixture
def user(client) -> tuple[str, str]:
    """(token, user_id) for a freshly registered user."""
    email = f"act-{uuid.uuid4().hex[:10]}@test.com"
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "username": f"act{uuid.uuid4().hex[:10]}",
            "password": "Str0ngPass!x",
            "full_name": "激活测试",
        },
    )
    assert r.status_code == 201, r.text

    async def _uid() -> str:
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import User

        async with AsyncSessionLocal() as db:
            uid = await db.scalar(select(User.id).where(User.email == email))
            return str(uid)

    return r.json()["access_token"], client.portal.call(_uid)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_node(client, host: str, port: int, name: str = "Node-A") -> str:
    async def _ins() -> str:
        from app.engine.session import AsyncSessionLocal
        from app.models import GatewayNode

        async with AsyncSessionLocal() as db:
            node = GatewayNode(host=host, port=port, status="online", name=name)
            db.add(node)
            await db.commit()
            return str(node.id)

    return client.portal.call(_ins)


def _seed_account(
    client,
    user_id: str,
    node_id: str,
    *,
    login: int | None = None,
    account_type: str = "real",
    with_password: bool = True,
) -> str:
    # (login, server) 唯一约束 → 未显式指定时生成随机 login
    if login is None:
        login = 700000 + int(uuid.uuid4().hex[:6], 16) % 299999

    async def _ins() -> str:
        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account
        from app.services import credentials

        async with AsyncSessionLocal() as db:
            acct = MT5Account(
                login=login,
                server="NodeBroker-Demo",
                name="Seed Account",
                account_type=account_type,
                currency="USD",
                gateway_node_id=uuid.UUID(node_id),
                user_id=uuid.UUID(user_id),
                password_encrypted=(
                    credentials.encrypt_secret("broker-pw") if with_password else None
                ),
            )
            db.add(acct)
            await db.commit()
            return str(acct.id)

    return client.portal.call(_ins)


def _audit_rows(client, target_id: str) -> list[dict]:
    async def _q() -> list[dict]:
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(AuditLog).where(AuditLog.target_id == target_id)
            )).scalars().all()
            return [{"action": r.action, "params": dict(r.params or {})} for r in rows]

    return client.portal.call(_q)


# ---------------------------------------------------------------------------
# POST /{account_id}/activate
# ---------------------------------------------------------------------------


def test_activate_success_routes_to_node_and_audits(client, stub, user):
    token, user_id = user
    node_id = _seed_node(client, "10.8.8.1", 8181, name="激活节点")
    acct_id = _seed_account(client, user_id, node_id, login=888001)

    r = client.post(f"{API}/{acct_id}/activate", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()

    # 探测走该账户绑定节点的 URL，凭据来自解密存储
    assert len(stub.instances) == 1
    assert stub.instances[0].base_url == "http://10.8.8.1:8181"
    assert stub.instances[0].logins == [(888001, "broker-pw", "NodeBroker-Demo")]

    # 行镜像刷新（余额/权益/连接状态；account_type 与 sync 一致不重镜像）
    assert body["id"] == acct_id
    assert body["balance"] == 7777.0
    assert body["equity"] == 7777.0
    assert body["account_type"] == "real"
    assert body["is_connected"] is True

    # 审计留痕
    rows = _audit_rows(client, acct_id)
    assert any(
        row["action"] == "account_activate"
        and row["params"].get("login") == 888001
        and row["params"].get("account_type") == "real"
        for row in rows
    )


def test_activate_gateway_login_rejected_400(client, stub, user):
    token, user_id = user
    node_id = _seed_node(client, "10.8.8.2", 8182)
    acct_id = _seed_account(client, user_id, node_id)

    stub.fail_login = "MT5 登录失败：(10006, 'Invalid account')"
    r = client.post(f"{API}/{acct_id}/activate", headers=_auth(token))
    assert r.status_code == 400
    assert "MT5 登录失败" in r.json()["detail"]
    assert _audit_rows(client, acct_id) == []  # 失败不写审计（探针先抛）


def test_activate_gateway_unreachable_503(client, stub, user):
    token, user_id = user
    node_id = _seed_node(client, "10.8.8.3", 8183)
    acct_id = _seed_account(client, user_id, node_id)

    stub.unavailable = True
    r = client.post(f"{API}/{acct_id}/activate", headers=_auth(token))
    assert r.status_code == 503
    assert "行情网关不可达" in r.json()["detail"]


def test_activate_other_user_404(client, stub, user):
    token, user_id = user
    node_id = _seed_node(client, "10.8.8.4", 8184)
    acct_id = _seed_account(client, user_id, node_id)

    r2 = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"act2-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"ac2{uuid.uuid4().hex[:8]}",
            "password": "Str0ngPass!x",
            "full_name": "其他用户",
        },
    )
    assert r2.status_code == 201

    r = client.post(f"{API}/{acct_id}/activate", headers=_auth(r2.json()["access_token"]))
    assert r.status_code == 404
    assert "账户不存在" in r.json()["detail"]


def test_activate_without_stored_password_400(client, stub, user):
    """绑定时未记住密码 → 无 body 激活 400，提示输入密码。"""
    token, user_id = user
    node_id = _seed_node(client, "10.8.8.5", 8185)
    acct_id = _seed_account(client, user_id, node_id, with_password=False)

    r = client.post(f"{API}/{acct_id}/activate", headers=_auth(token))
    assert r.status_code == 400
    assert "未记住密码" in r.json()["detail"]
    assert not stub.instances  # 未发起任何网关登录


def test_activate_with_password_override(client, stub, user):
    """未记住密码的账户：激活时携带本次密码（仅本次登录，不落库）。"""
    token, user_id = user
    port = 30000 + int(uuid.uuid4().hex[:4], 16) % 30000
    node_id = _seed_node(client, "10.8.8.6", port)
    acct_id = _seed_account(client, user_id, node_id, login=888006, with_password=False)

    r = client.post(
        f"{API}/{acct_id}/activate", headers=_auth(token), json={"password": "typed-pw"},
    )
    assert r.status_code == 200, r.text
    assert stub.instances[0].base_url == f"http://10.8.8.6:{port}"
    assert stub.instances[0].logins == [(888006, "typed-pw", "NodeBroker-Demo")]
    # 密码仅本次使用：不落库
    assert r.json()["has_credentials"] is False


def test_bind_remember_password_flag_controls_storage(client, stub, user):
    """绑定「记住密码」开关：默认记住（加密存储），关闭则不落库。"""
    token, _ = user
    port_a = 30000 + int(uuid.uuid4().hex[:4], 16) % 30000
    port_b = 30000 + int(uuid.uuid4().hex[:4], 16) % 30000
    node_a = _seed_node(client, "10.8.9.1", port_a, name="绑定节点A")
    node_b = _seed_node(client, "10.8.9.2", port_b, name="绑定节点B")

    r1 = client.post(API, headers=_auth(token), json={
        "login": 913350111, "password": "pw-remember", "server": "Srv-A",
        "gateway_node_id": node_a,
    })
    assert r1.status_code == 201, r1.text
    assert r1.json()["has_credentials"] is True

    r2 = client.post(API, headers=_auth(token), json={
        "login": 913350222, "password": "pw-ephemeral", "server": "Srv-B",
        "gateway_node_id": node_b, "remember_password": False,
    })
    assert r2.status_code == 201, r2.text
    assert r2.json()["has_credentials"] is False

    async def _stored() -> tuple[str | None, str | None]:
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account

        async with AsyncSessionLocal() as db:
            a = await db.scalar(
                select(MT5Account.password_encrypted).where(MT5Account.login == 913350111)
            )
            b = await db.scalar(
                select(MT5Account.password_encrypted).where(MT5Account.login == 913350222)
            )
            return a, b

    stored_a, stored_b = client.portal.call(_stored)
    assert stored_a is not None and stored_a.startswith("v1:")
    assert stored_b is None


# ---------------------------------------------------------------------------
# GET /active
# ---------------------------------------------------------------------------


def test_active_lists_current_login_per_node(client, stub, user):
    token, user_id = user
    node_id = _seed_node(client, "10.8.8.6", 8186, name="行情网关A")
    _seed_account(client, user_id, node_id)

    r = client.get(f"{API}/active", headers=_auth(token))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["node_id"] == node_id
    assert items[0]["node_name"] == "行情网关A"
    assert items[0]["account"]["login"] == 888001
    assert items[0]["account"]["account_type"] == "real"
    # 探活打的是节点自己的 URL
    assert any(i.base_url == "http://10.8.8.6:8186" for i in stub.instances)


def test_active_node_unreachable_account_null(client, stub, user):
    token, user_id = user
    node_id = _seed_node(client, "10.8.8.7", 8187)
    _seed_account(client, user_id, node_id)

    stub.fail_health = True
    r = client.get(f"{API}/active", headers=_auth(token))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["node_id"] == node_id
    assert items[0]["account"] is None  # 离线节点 → null，前端显示离线


def test_active_no_accounts_returns_empty(client, stub, user):
    token, _ = user
    r = client.get(f"{API}/active", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_active_ea_bridge_node_synthesizes_from_row(client, stub, user):
    """EA 桥节点无 HTTP 服务：用绑定行合成快照，不探活（不新建 client）。"""
    token, user_id = user

    async def _ins() -> str:
        from app.engine.session import AsyncSessionLocal
        from app.models import GatewayNode, MT5Account

        async with AsyncSessionLocal() as db:
            node = GatewayNode(
                host="ea:NodeBroker-Demo:5040012345", port=0, status="online",
                name="EA 桥节点", type="ea_bridge",
            )
            db.add(node)
            await db.flush()
            acct = MT5Account(
                login=5040012345, server="NodeBroker-Demo", name="EA Account",
                account_type="demo", currency="USD",
                gateway_node_id=node.id, user_id=uuid.UUID(user_id),
                password_encrypted=None,
            )
            db.add(acct)
            await db.commit()
            return "ok"

    client.portal.call(_ins)

    r = client.get(f"{API}/active", headers=_auth(token))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["node_name"] == "EA 桥节点"
    assert items[0]["account"]["login"] == 5040012345
    assert items[0]["account"]["account_type"] == "demo"
    assert not stub.instances  # 未发起任何 HTTP 探活
