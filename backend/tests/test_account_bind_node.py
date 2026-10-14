"""In-flow account binding to a chosen gateway node (M13).

The paper / live dialogs let the user type account+password+server and bind
directly.  The backend must then (a) probe THAT node's URL and (b) persist the
account on that node, so every later run routes to it (M12 invariant).
"""

from __future__ import annotations

import uuid

import pytest

API = "/api/v1/accounts"


class RecordingStub:
    """GatewayClient replacement recording the base_url of every instance."""

    instances: list["RecordingStub"] = []

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url
        self.logins: list[tuple] = []
        RecordingStub.instances.append(self)

    async def login(self, login: int, password: str, server: str) -> dict:
        self.logins.append((login, password, server))
        return {
            "login": login,
            "server": server,
            "name": "Inline Bind Account",
            "type": "demo",
            "leverage": 200,
            "currency": "USD",
            "balance": 5000.0,
            "credit": 0.0,
            "equity": 5000.0,
            "margin": 0.0,
            "margin_free": 5000.0,
            "margin_level": 0.0,
            "trade_allowed": True,
        }

    async def aclose(self) -> None:
        pass


@pytest.fixture
def stub(monkeypatch):
    import app.api.v1.endpoints.accounts as acc_mod
    from app.main import app
    from app.services.gateway_client import get_gateway_client

    RecordingStub.instances = []
    monkeypatch.setattr(acc_mod, "GatewayClient", RecordingStub)

    class _Default:
        """Injected default client (legacy path): records logins, no URL."""

        def __init__(self) -> None:
            self.logins: list[tuple] = []

        async def login(self, login, password, server):
            self.logins.append((login, password, server))
            return {
                "login": login, "server": server, "name": "Default", "type": "demo",
                "leverage": 100, "currency": "USD", "balance": 1.0, "credit": 0.0,
                "equity": 1.0, "margin": 0.0, "margin_free": 1.0, "margin_level": 0.0,
                "trade_allowed": True,
            }

    default = _Default()
    RecordingStub.default = default  # tests assert via stub.default.logins
    app.dependency_overrides[get_gateway_client] = lambda: default
    yield RecordingStub
    app.dependency_overrides.pop(get_gateway_client, None)


@pytest.fixture
def token(client) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"ib-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"ib{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "流程内绑定"},
    )
    assert r.status_code == 201
    return r.json()["access_token"]


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _seed_node(client, host: str, port: int) -> str:
    async def _ins() -> str:
        from app.engine.session import AsyncSessionLocal
        from app.models import GatewayNode

        async with AsyncSessionLocal() as db:
            node = GatewayNode(host=host, port=port, status="online")
            db.add(node)
            await db.commit()
            return str(node.id)

    return client.portal.call(_ins)


def _node_account_id(client, node_id: str) -> str | None:
    async def _q() -> str | None:
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account

        async with AsyncSessionLocal() as db:
            return await db.scalar(
                select(MT5Account.id).where(MT5Account.gateway_node_id == uuid.UUID(node_id))
            )

    rid = client.portal.call(_q)
    return str(rid) if rid else None


def test_bind_to_selected_node_probes_that_url(client, token, stub):
    node_id = _seed_node(client, "10.9.9.1", 8191)
    r = client.post(
        API,
        json={"gateway_node_id": node_id, "login": 777001,
              "password": "pw!", "server": "NodeBroker-Demo"},
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["gateway_node_id"] == node_id
    assert body["login"] == 777001
    # 探测走的是所选节点的 URL（流程内绑定 = 服务器即该节点）
    assert any(i.base_url == "http://10.9.9.1:8191" for i in stub.instances)
    assert _node_account_id(client, node_id) == body["id"]


def test_bind_unknown_node_404(client, token, stub):
    r = client.post(
        API,
        json={"gateway_node_id": str(uuid.uuid4()), "login": 777002,
              "password": "pw!", "server": "X"},
        headers=_auth(token),
    )
    assert r.status_code == 404
    assert "网关节点" in r.json()["detail"]


def test_test_endpoint_routes_to_node(client, token, stub):
    node_id = _seed_node(client, "10.9.9.2", 8192)
    r = client.post(
        f"{API}/test",
        json={"gateway_node_id": node_id, "login": 777003,
              "password": "pw!", "server": "Y"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert any(i.base_url == "http://10.9.9.2:8192" for i in stub.instances)


def test_bind_without_node_falls_back_default(client, token, stub):
    from app.services.gateway_heartbeat import gateway_host_port

    r = client.post(
        API,
        json={"login": 777004, "password": "pw!", "server": "Z"},
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    # 未指定节点 → 用注入的默认 client，账户落默认节点
    assert stub.default.logins == [(777004, "pw!", "Z")]
    assert not stub.instances  # 未新建任何按节点 URL 的 client

    host, port = gateway_host_port()

    async def _q() -> tuple:
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import GatewayNode, MT5Account

        async with AsyncSessionLocal() as db:
            acct = (await db.execute(
                select(MT5Account, GatewayNode)
                .join(GatewayNode, MT5Account.gateway_node_id == GatewayNode.id)
                .where(MT5Account.login == 777004)
            )).first()
            return acct[1].host, acct[1].port

    assert client.portal.call(_q) == (host, port)
