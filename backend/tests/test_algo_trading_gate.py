"""允许算法交易开关（账户级）：端点 + live 下单前置闸。

- POST /accounts/{id}/algo-trading 切换 + 审计（flag_change）+ 归属 404
  响应为 {account, terminal_sync}：terminal_sync 反映 MT5 终端本体
  「算法交易」按钮（Ctrl+E 注入）的同步结果（EA 桥节点跳过 → ok=True）。
- algo_trading_enabled=False 时 live manager 拒绝策略自动下单
  （error_code=ALGO_TRADING_DISABLED），True 时不拦截。
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.gateway_client import GatewayError

API = "/api/v1/accounts"


# ── 网关 stub（终端按钮同步） ─────────────────────────────────

class StubGatewayClient:
    """set_terminal_algo 可编程：正常返回 / 抛 GatewayError。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[bool] = []

    async def set_terminal_algo(self, enabled: bool, timeout: float | None = None) -> dict:
        self.calls.append(enabled)
        if self.fail:
            raise GatewayError("找不到 MT5 终端窗口")
        return {"changed": True, "trade_allowed": enabled,
                "message": "终端「算法交易」按钮已同步"}


@pytest.fixture
def gw_stub():
    from app.main import app
    from app.services.gateway_client import get_gateway_client

    stub = StubGatewayClient()
    app.dependency_overrides[get_gateway_client] = lambda: stub
    yield stub
    app.dependency_overrides.pop(get_gateway_client, None)


@pytest.fixture
def token(client) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"algo-{uuid.uuid4().hex[:10]}@test.com",
            "username": f"algo{uuid.uuid4().hex[:10]}",
            "password": "Passw0rd!",
            "full_name": "算法开关测试",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _uid(client, token: str) -> str:
    from jose import jwt

    from app.config import settings

    return jwt.decode(token, settings.JWT_SECRET_KEY,
                      algorithms=[settings.JWT_ALGORITHM])["sub"]


def _seed(client, token: str, *, algo_enabled: bool) -> dict:
    """直接落库：网关节点 + 策略 + 账户（含开关位）+ 运行中的 live run。"""

    async def _ins():
        from app.engine.session import AsyncSessionLocal
        from app.models import GatewayNode, LiveRun, MT5Account, Strategy

        uid = uuid.UUID(_uid(client, token))
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select as _select

            node = (await db.execute(
                _select(GatewayNode).where(GatewayNode.host == "127.0.0.1",
                                           GatewayNode.port == 8090)
            )).scalars().first()
            if node is None:
                node = GatewayNode(host="127.0.0.1", port=8090, status="online")
                db.add(node)
                await db.flush()
            strat = Strategy(user_id=uid, name=f"algo-{uuid.uuid4().hex[:8]}",
                             timeframe="H1", status="active")
            db.add(strat)
            acct = MT5Account(
                login=830000 + (uuid.uuid4().int % 9000),
                server="MetaQuotes-Demo", name="t", account_type="demo",
                leverage=100, currency="USD", gateway_node_id=node.id,
                user_id=uid, live_allowed=True,
                algo_trading_enabled=algo_enabled,
            )
            db.add(acct)
            await db.flush()
            run = LiveRun(
                user_id=uid, strategy_id=strat.id, account_id=acct.id,
                source_code="x", symbol="EURUSD", timeframe="H1",
                execution_mode="standard", allocated_capital=Decimal("1000"),
                leverage=100, magic=8801, positions=[], status="running",
                balance=Decimal("1000"), equity=Decimal("1000"),
                margin_free=Decimal("1000"),
            )
            db.add(run)
            await db.commit()
            return {"run_id": str(run.id), "account_id": str(acct.id)}

    return client.portal.call(_ins)


# ── 端点 ──────────────────────────────────────────────────────

def test_algo_trading_toggle_and_audit(client, token, gw_stub):
    seeded = _seed(client, token, algo_enabled=True)
    aid = seeded["account_id"]

    r = client.get(API, headers=_auth(token))
    assert r.status_code == 200
    row = next(i for i in r.json()["items"] if i["id"] == aid)
    assert row["algo_trading_enabled"] is True

    # 关闭（终端按钮同步成功）
    r = client.post(f"{API}/{aid}/algo-trading", headers=_auth(token),
                    json={"enabled": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["account"]["algo_trading_enabled"] is False
    assert body["terminal_sync"]["ok"] is True
    assert body["terminal_sync"]["trade_allowed"] is False
    assert gw_stub.calls == [False]

    # 再打开（幂等路径：同值不重复审计；同步仍执行）
    r = client.post(f"{API}/{aid}/algo-trading", headers=_auth(token),
                    json={"enabled": True})
    assert r.status_code == 200
    body = r.json()
    assert body["account"]["algo_trading_enabled"] is True
    assert body["terminal_sync"]["ok"] is True
    assert gw_stub.calls == [False, True]

    async def _audit_rows():
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(AuditLog).where(AuditLog.action == "flag_change")
            )).scalars().all()
            return [(a.params.get("flag"), a.params.get("enabled")) for a in rows]

    flags = client.portal.call(_audit_rows)
    assert ("algo_trading_enabled", False) in flags
    assert ("algo_trading_enabled", True) in flags


def test_algo_trading_requires_ownership(client, token, gw_stub):
    seeded = _seed(client, token, algo_enabled=True)

    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"other-{uuid.uuid4().hex[:8]}@test.com",
              "username": f"other{uuid.uuid4().hex[:8]}",
              "password": "Passw0rd!", "full_name": "别人"},
    )
    other = r.json()["access_token"]
    r = client.post(f"{API}/{seeded['account_id']}/algo-trading",
                    headers=_auth(other), json={"enabled": False})
    assert r.status_code == 404

    r = client.post(f"{API}/{uuid.uuid4()}/algo-trading",
                    headers=_auth(token), json={"enabled": False})
    assert r.status_code == 404


def test_algo_trading_terminal_sync_failure(client, token):
    """网关侧终端按钮切换失败 → 200 + terminal_sync.ok=False，平台开关仍持久化。"""
    from app.main import app
    from app.services.gateway_client import get_gateway_client

    stub = StubGatewayClient(fail=True)
    app.dependency_overrides[get_gateway_client] = lambda: stub
    try:
        seeded = _seed(client, token, algo_enabled=True)
        aid = seeded["account_id"]
        r = client.post(f"{API}/{aid}/algo-trading", headers=_auth(token),
                        json={"enabled": False})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["account"]["algo_trading_enabled"] is False
        assert body["terminal_sync"]["ok"] is False
        assert "找不到 MT5 终端窗口" in body["terminal_sync"]["message"]

        r = client.get(API, headers=_auth(token))
        row = next(i for i in r.json()["items"] if i["id"] == aid)
        assert row["algo_trading_enabled"] is False
    finally:
        app.dependency_overrides.pop(get_gateway_client, None)


# ── live 下单前置闸 ───────────────────────────────────────────

class FakeSup:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, cmd: dict) -> bool:
        self.sent.append(cmd)
        return True


def _mk_handle(**kw) -> SimpleNamespace:
    base = dict(
        busy=False, sup=FakeSup(), account={}, positions=[], halt_until=0.0,
        day_start_equity=0.0, peak_equity=0.0, order_times=[], magic=8801,
        gateway_url="", run_id="", symbol="EURUSD", timeframe="H1",
        rules_loaded_at=0.0, rules=None, last_account_write=0.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _order_result(sup: FakeSup) -> dict:
    rs = [m for m in sup.sent if m.get("type") == "order_result"]
    assert rs, f"no order_result sent: {sup.sent}"
    return rs[-1]


def test_algo_disabled_rejects_strategy_order(client, token):
    from app.services.live import manager as live_manager_mod

    seeded = _seed(client, token, algo_enabled=False)
    mgr = live_manager_mod.LiveManager()
    h = _mk_handle(run_id=seeded["run_id"])
    mgr._handles[seeded["run_id"]] = h
    req = {"req_id": "rq-1", "op": "buy", "symbol": "EURUSD", "volume": 0.1,
           "position_id": None, "comment": ""}
    client.portal.call(mgr._handle_order_request, seeded["run_id"], req)

    res = _order_result(h.sup)
    assert res["success"] is False
    assert res["error_code"] == "ALGO_TRADING_DISABLED"
    assert "算法交易" in (res["error_message"] or "")


def test_algo_enabled_order_passes_gate(client, token, monkeypatch):
    from app.services.live import manager as live_manager_mod
    from app.services.risk.engine import RiskVerdict

    seeded = _seed(client, token, algo_enabled=True)
    monkeypatch.setattr(
        live_manager_mod, "evaluate",
        lambda **k: RiskVerdict(allow=True, error_code=None, error_message=None),
    )

    class StubGW:
        def __init__(self, *a, **k) -> None: ...
        async def place_market(self, *a, **k) -> dict:
            return {"success": True, "order": 111, "deal": 222,
                    "price": 1.11, "volume": 0.1}
        async def account(self, timeout=None) -> None:
            return None
        async def positions(self, symbol=None, timeout=None) -> list:
            return []
        async def aclose(self) -> None: ...

    monkeypatch.setattr(live_manager_mod, "GatewayClient", StubGW)

    mgr = live_manager_mod.LiveManager()
    h = _mk_handle(
        run_id=seeded["run_id"], account_id=seeded["account_id"],
        account={"bid": 1.1, "ask": 1.1002, "balance": 1000.0, "equity": 1000.0},
    )
    mgr._handles[seeded["run_id"]] = h
    req = {"req_id": "rq-2", "op": "buy", "symbol": "EURUSD", "volume": 0.1,
           "position_id": None, "comment": "", "bid": 1.1, "ask": 1.1002}
    client.portal.call(mgr._handle_order_request, seeded["run_id"], req)

    res = _order_result(h.sup)
    assert res["error_code"] != "ALGO_TRADING_DISABLED"
    assert res["success"] is True
    assert res["filled_price"] == 1.11
