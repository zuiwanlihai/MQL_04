"""实盘/虚拟下单互斥后端双保险（总览账户登录 al-3）.

- paper 创建：绑定行非 demo → 409；节点当前登录 real（探活）→ 409
- live 部署按 scope 划分：
  - scope=real（实盘）：绑定行/当前登录须 real，否则 409；全闸（回测/观察期/风控）
  - scope=demo（模拟盘交易）：绑定行须 demo，免准入四闸、免观察期
- admission 权限闸：demo+资格 通过；竞赛账户拒绝（防绕过创建端点的路径）
- probe helper：health 映射 / 不可达 None / 开关关闭 None

探活在测试套件默认关闭（conftest 设 GATEWAY_MUTEX_PROBE_ENABLED=false，
否则会打到 127.0.0.1:8090 的真实网关破坏封闭性）——探针行为用类级 stub
单独覆盖。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

PAPER_API = "/api/v1/paper"
LIVE_API = "/api/v1/live"
STRAT_API = "/api/v1/strategies"

STRATEGY_SRC = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_bar(ctx, bar):
    if ctx.storage.get("opened"):
        return
    ctx.storage["opened"] = True
    ctx.buy("EURUSD", 0.1)

def on_order(ctx, order):
    pass
"""


class ProbeStub:
    """gateway_client.GatewayClient replacement for the mutex probe."""

    account: dict | None = None
    fail = False
    instances: list["ProbeStub"] = []

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url
        ProbeStub.instances.append(self)

    @classmethod
    def for_registered_node(cls, base_url: str | None = None) -> ProbeStub:
        # 被测代码走 GatewayClient.for_registered_node()：桩要能应答。
        return cls(base_url)

    async def health(self) -> dict:
        if ProbeStub.fail:
            from app.services.gateway_client import GatewayError

            raise GatewayError("行情网关不可达：stub down")
        return {} if ProbeStub.account is None else {"account": dict(ProbeStub.account)}

    async def aclose(self) -> None:
        pass


@pytest.fixture()
def token(client: TestClient) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"mx-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"mx{uuid.uuid4().hex[:8]}",
            "password": "Str0ngPass!x",
            "full_name": "互斥测试",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _uid_of(client: TestClient, token: str) -> str:
    from jose import jwt

    from app.config import settings

    return jwt.decode(token, settings.JWT_SECRET_KEY,
                      algorithms=[settings.JWT_ALGORITHM])["sub"]


@pytest.fixture()
def strategy(client: TestClient, token: str) -> dict:
    """strategy_id + frozen v1（实盘部署要求冻结版本）."""
    sid = client.post(
        STRAT_API, json={"name": f"mx-{uuid.uuid4().hex[:8]}", "timeframe": "H1"},
        headers=_auth(token),
    ).json()["id"]
    r = client.put(f"{STRAT_API}/{sid}/source",
                   json={"source_code": STRATEGY_SRC}, headers=_auth(token))
    assert r.status_code == 200, r.text
    ver = client.post(f"{STRAT_API}/{sid}/versions",
                      json={"changelog": "v1"}, headers=_auth(token))
    assert ver.status_code == 201, ver.text
    return {"id": sid, "version": ver.json()["version"]}


def _seed_account(
    client: TestClient,
    token: str,
    *,
    account_type: str = "demo",
    host: str = "10.7.7.1",
    port: int = 8171,
) -> str:
    """Direct-DB seed: node + MT5Account for the token's user → account_id."""
    from app.services import credentials

    uid = _uid_of(client, token)

    async def _ins() -> str:
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import GatewayNode, MT5Account

        async with AsyncSessionLocal() as db:
            node = (await db.execute(
                select(GatewayNode).where(
                    GatewayNode.host == host, GatewayNode.port == port)
            )).scalars().first()
            if node is None:
                node = GatewayNode(host=host, port=port, status="online")
                db.add(node)
                await db.flush()
            acct = MT5Account(
                login=760000 + (uuid.uuid4().int % 90000),
                server="MetaQuotes-Demo", name="mx", account_type=account_type,
                leverage=100, currency="USD", gateway_node_id=node.id,
                user_id=uuid.UUID(uid), live_allowed=account_type == "real",
                password_encrypted=credentials.encrypt_secret("broker-pw"),
            )
            db.add(acct)
            await db.commit()
            return str(acct.id)

    return client.portal.call(_ins)


def _paper_body(strategy_id: str, account_id: str) -> dict:
    return {
        "strategy_id": strategy_id, "account_id": account_id, "version": None,
        "symbol": "EURUSD", "timeframe": "H1", "initial_deposit": 10000.0,
        "leverage": 100, "execution_mode": "standard", "spread_mode": "current",
        "warmup_bars": 100, "parameters": {}, "assumption_ack": True,
    }


def _live_body(strategy: dict, account_id: str, login: int) -> dict:
    return {
        "strategy_id": strategy["id"], "version": strategy["version"],
        "account_id": account_id, "symbol": "EURUSD", "timeframe": "H1",
        "allocated_capital": 500.0, "leverage": 100, "warmup_bars": 100,
        "parameters": {},
        "rules": [{"type": "daily_max_loss", "threshold": 50.0,
                   "action": "halt_open_today"}],
        "disclaimer_ack": True,
        "account_tail": str(login)[-4:],
    }


# ---------------------------------------------------------------------------
# paper 创建：虚拟下单仅限模拟账户
# ---------------------------------------------------------------------------


def test_paper_create_rejects_real_bound_account(client, token, strategy):
    acct_id = _seed_account(client, token, account_type="real")
    r = client.post(PAPER_API, json=_paper_body(strategy["id"], acct_id),
                    headers=_auth(token))
    assert r.status_code == 409, r.text
    assert "虚拟下单仅支持模拟账户" in r.json()["detail"]
    assert "实盘账户" in r.json()["detail"]


def test_paper_create_rejects_real_active_login(client, token, strategy, monkeypatch):
    """绑定行是 demo，但节点当前登录的是实盘账户 → 探活 409。"""
    import app.services.gateway_client as gw_mod
    import app.services.gateway_route as route_mod

    monkeypatch.setattr(route_mod.settings, "GATEWAY_MUTEX_PROBE_ENABLED", True)
    monkeypatch.setattr(gw_mod, "GatewayClient", ProbeStub)
    ProbeStub.instances = []
    ProbeStub.account = {"login": 2015611, "type": "real"}

    acct_id = _seed_account(client, token, account_type="demo")
    r = client.post(PAPER_API, json=_paper_body(strategy["id"], acct_id),
                    headers=_auth(token))
    assert r.status_code == 409, r.text
    assert "当前登录的是实盘账户" in r.json()["detail"]
    # 探活打的是该账户绑定节点
    assert any(i.base_url == "http://10.7.7.1:8171" for i in ProbeStub.instances)


def test_paper_create_allows_demo_when_probe_unreachable(client, token, strategy):
    """探活关闭（conftest 默认）+ demo 绑定行 → 正常创建（兜底路径）。"""
    acct_id = _seed_account(client, token, account_type="demo")
    r = client.post(PAPER_API, json=_paper_body(strategy["id"], acct_id),
                    headers=_auth(token))
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# live 部署：实盘/模拟账户均可（模拟=订单发往 MT5 模拟服务器）；竞赛账户拒绝
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _live_enabled(monkeypatch):
    from app.api.v1.endpoints import live as live_mod

    monkeypatch.setattr(live_mod.settings, "LIVE_ENABLED", True)


def test_live_deploy_rejects_demo_bound_account(client, token, strategy):
    """scope=real（默认实盘）：demo 绑定行 → 类型互斥 409。"""
    acct_id = _seed_account(client, token, account_type="demo")

    async def _login():
        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account

        async with AsyncSessionLocal() as db:
            return (await db.get(MT5Account, uuid.UUID(acct_id))).login

    login = client.portal.call(_login)
    r = client.post(LIVE_API, json=_live_body(strategy, acct_id, login),
                    headers=_auth(token))
    assert r.status_code == 409, r.text
    assert "实盘仅支持实盘账户" in r.json()["detail"]
    assert "模拟" in r.json()["detail"]


def test_live_deploy_demo_scope_skips_gates(client, token, strategy, monkeypatch):
    """scope=demo（模拟盘交易）：demo 账户免准入四闸直接创建，免观察期。"""
    from app.services.live.manager import get_live_manager

    monkeypatch.setattr(get_live_manager(), "spawn_start", lambda run_id: None)

    acct_id = _seed_account(client, token, account_type="demo")

    async def _login():
        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account

        async with AsyncSessionLocal() as db:
            return (await db.get(MT5Account, uuid.UUID(acct_id))).login

    login = client.portal.call(_login)
    body = {**_live_body(strategy, acct_id, login), "scope": "demo"}
    r = client.post(LIVE_API, json=body, headers=_auth(token))
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["account_type"] == "demo"
    assert data["observation_ends_at"] is None
    detail = client.get(f"{LIVE_API}/{data['id']}", headers=_auth(token)).json()
    assert detail["admission_snapshot"] == {
        "scope": "demo", "gates": "skipped", "note": "模拟盘交易免准入四闸",
    }


def test_live_deploy_demo_scope_rejects_real_account(client, token, strategy):
    """scope=demo：实盘绑定行 → 409。"""
    acct_id = _seed_account(client, token, account_type="real")

    async def _login():
        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account

        async with AsyncSessionLocal() as db:
            return (await db.get(MT5Account, uuid.UUID(acct_id))).login

    login = client.portal.call(_login)
    body = {**_live_body(strategy, acct_id, login), "scope": "demo"}
    r = client.post(LIVE_API, json=body, headers=_auth(token))
    assert r.status_code == 409, r.text
    assert "模拟盘交易仅支持模拟账户" in r.json()["detail"]


def test_live_deploy_rejects_contest_active_login(client, token, strategy, monkeypatch):
    """绑定行是 real，但节点当前登录的是竞赛账户 → 探活 409。"""
    import app.services.gateway_client as gw_mod
    import app.services.gateway_route as route_mod

    monkeypatch.setattr(route_mod.settings, "GATEWAY_MUTEX_PROBE_ENABLED", True)
    monkeypatch.setattr(gw_mod, "GatewayClient", ProbeStub)
    ProbeStub.instances = []
    ProbeStub.account = {"login": 3000001, "type": "contest"}

    acct_id = _seed_account(client, token, account_type="real")

    async def _login():
        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account

        async with AsyncSessionLocal() as db:
            return (await db.get(MT5Account, uuid.UUID(acct_id))).login

    login = client.portal.call(_login)
    r = client.post(LIVE_API, json=_live_body(strategy, acct_id, login),
                    headers=_auth(token))
    assert r.status_code == 409, r.text
    assert "实盘要求当前登录的是实盘账户" in r.json()["detail"]


def test_admission_permission_gate_demo_passes_contest_rejected(client, token, strategy):
    """demo+资格 → 权限闸通过；竞赛账户 → 拒绝（防绕过创建端点的路径）。"""
    demo_id = _seed_account(client, token, account_type="demo")

    async def _allow_demo():
        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account

        async with AsyncSessionLocal() as db:
            acct = await db.get(MT5Account, uuid.UUID(demo_id))
            acct.live_allowed = True
            await db.commit()

    client.portal.call(_allow_demo)

    body = {"strategy_id": strategy["id"], "version": strategy["version"],
            "account_id": demo_id}
    r1 = client.post(f"{LIVE_API}/wizard/check-admission", json=body,
                     headers=_auth(token))
    assert r1.status_code == 200, r1.text
    gates1 = {g["key"]: g for g in r1.json()["gates"]}
    assert gates1["permission"]["passed"] is True

    contest_id = _seed_account(client, token, account_type="contest")

    async def _allow_contest():
        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account

        async with AsyncSessionLocal() as db:
            acct = await db.get(MT5Account, uuid.UUID(contest_id))
            acct.live_allowed = True
            await db.commit()

    client.portal.call(_allow_contest)

    r2 = client.post(f"{LIVE_API}/wizard/check-admission",
                     json={**body, "account_id": contest_id},
                     headers=_auth(token))
    assert r2.status_code == 200, r2.text
    gates2 = {g["key"]: g for g in r2.json()["gates"]}
    assert gates2["permission"]["passed"] is False
    assert "实盘部署仅支持实盘/模拟账户" in gates2["permission"]["reason"]


# ---------------------------------------------------------------------------
# probe helper 单元行为
# ---------------------------------------------------------------------------


def test_probe_maps_health_and_failures(client, monkeypatch):
    import app.services.gateway_client as gw_mod
    import app.services.gateway_route as route_mod

    monkeypatch.setattr(route_mod.settings, "GATEWAY_MUTEX_PROBE_ENABLED", True)
    monkeypatch.setattr(gw_mod, "GatewayClient", ProbeStub)

    async def _run(db, node_id):
        return await route_mod.probe_active_account_type(db, node_id)

    def _node_id() -> str:
        async def _ins() -> str:
            from app.engine.session import AsyncSessionLocal
            from app.models import GatewayNode

            async with AsyncSessionLocal() as db:
                node = GatewayNode(host="10.7.7.9", port=8179, status="online")
                db.add(node)
                await db.commit()
                return str(node.id)

        return client.portal.call(_ins)

    node_id = uuid.UUID(_node_id())

    async def _cases() -> tuple:
        from app.engine.session import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            ProbeStub.account = {"type": "real"}
            real = await route_mod.probe_active_account_type(session, node_id)
            ProbeStub.account = {"account_type": "demo"}  # 兼容 account_type 键
            demo = await route_mod.probe_active_account_type(session, node_id)
            ProbeStub.fail = True
            unknown = await route_mod.probe_active_account_type(session, node_id)
            ProbeStub.fail = False
            ProbeStub.account = None  # health 无 account 键 → 未知
            empty = await route_mod.probe_active_account_type(session, node_id)
            monkeypatch.setattr(
                route_mod.settings, "GATEWAY_MUTEX_PROBE_ENABLED", False)
            off = await route_mod.probe_active_account_type(session, node_id)
            monkeypatch.setattr(
                route_mod.settings, "GATEWAY_MUTEX_PROBE_ENABLED", True)
            gone = await route_mod.probe_active_account_type(
                session, uuid.uuid4())  # 节点不存在
        return real, demo, unknown, empty, off, gone

    real, demo, unknown, empty, off, gone = client.portal.call(_cases)
    assert real == "real"
    assert demo == "demo"
    assert unknown is None   # GatewayError → None（fail-open）
    assert empty is None     # 无 account → None
    assert off is None       # 开关关闭 → None
    assert gone is None      # 节点不存在 → None
