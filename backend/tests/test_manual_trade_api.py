"""Manual trading panel endpoint tests (M14 Phase B).

GatewayClient is stubbed at the manual_trade module level.  Covers: feature
flag 409, ownership 404, risk-gate block (rule + platform lot cap + frozen
account), pass-through order/close/cancel with audit rows, read-only account
rejection, and the snapshot read path.
"""

from __future__ import annotations

import uuid

import pytest

API = "/api/v1/trade"


class StubGW:
    """Scriptable gateway stub (login → mirror → trade)."""

    def __init__(self, base_url=None):
        self.base_url = base_url
        self.orders_sent: list[dict] = []
        self.closes: list[dict] = []
        self.cancels: list[int] = []
        self.fail_login = False
        self.reject: dict | None = None  # broker-side rejection payload

    async def login(self, login, password, server):
        if self.fail_login:
            from app.services.gateway_client import GatewayLoginError

            raise GatewayLoginError("MT5 登录失败：密码错误")
        return {"login": login, "server": server}

    async def account(self, timeout=None):
        return {"login": 1, "equity": 10000.0, "balance": 10000.0,
                "margin_free": 9000.0, "margin": 100.0}

    async def positions(self, symbol=None, timeout=None):
        return []

    async def orders(self, symbol=None, timeout=None):
        return [{"ticket": 501, "symbol": "EURUSD", "volume": 0.1}]

    async def deals(self, start, end, symbol=None, timeout=None):
        return [{"ticket": 900, "symbol": "EURUSD", "profit": 1.0}]

    async def place_market(self, symbol, volume, side, magic=0, comment="",
                           deviation=20, timeout=None):
        self.orders_sent.append({"symbol": symbol, "volume": volume, "side": side,
                                 "magic": magic})
        if self.reject is not None:
            return dict(self.reject)
        return {"success": True, "retcode": 10009, "retcode_text": "成交",
                "order": 8001, "deal": 8002, "price": 1.1, "volume": volume}

    async def close_position(self, ticket, volume=None, magic=0, deviation=20,
                             timeout=None):
        self.closes.append({"ticket": ticket, "volume": volume, "magic": magic})
        return {"success": True, "retcode": 10009, "retcode_text": "成交",
                "order": 8003, "deal": 8004, "price": 1.1, "volume": volume or 0.1}

    async def cancel_order(self, ticket, timeout=None):
        self.cancels.append(ticket)
        return {"success": True, "retcode": 10009, "retcode_text": "已撤单",
                "order": ticket, "deal": 0, "price": 0.0, "volume": 0.0}

    async def aclose(self):
        pass


@pytest.fixture
def stub(monkeypatch):
    import app.services.manual_trade as mt_mod

    holder: dict = {}

    def factory(base_url=None):
        s = StubGW(base_url)
        holder["gw"] = s
        if holder.get("fail_login"):
            s.fail_login = True
        if holder.get("reject") is not None:
            s.reject = holder["reject"]
        return s

    # 被测代码走 GatewayClient.for_registered_node(url)：替代品要能应答，
    # 否则用例会红在「接口不匹配」上。凭据该不该带见 test_gateway_key_scope.py。
    factory.for_registered_node = factory

    monkeypatch.setattr(mt_mod, "GatewayClient", factory)
    yield holder


@pytest.fixture
def token(client) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"mt-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"mt{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "手动交易"},
    )
    assert r.status_code == 201
    return r.json()["access_token"]


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _uid(client, token) -> str:
    from jose import jwt

    from app.config import settings

    return jwt.decode(token, settings.JWT_SECRET_KEY,
                      algorithms=[settings.JWT_ALGORITHM])["sub"]


def _seed_account(client, token, *, password="Secret123!",
                  trade_allowed=True, frozen=False,
                  host="10.20.0.1", port=8201) -> str:
    from app.services import credentials

    uid = _uid(client, token)

    async def _ins() -> str:
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import GatewayNode, MT5Account

        async with AsyncSessionLocal() as db:
            node = (await db.execute(
                select(GatewayNode).where(GatewayNode.host == host,
                                          GatewayNode.port == port)
            )).scalars().first()
            if node is None:
                node = GatewayNode(host=host, port=port, status="online")
                db.add(node)
                await db.flush()
            acct = MT5Account(
                login=950000 + (uuid.uuid4().int % 90000),
                server="MetaQuotes-Demo", name="mt", account_type="demo",
                leverage=100, currency="USD", gateway_node_id=node.id,
                user_id=uuid.UUID(uid), live_allowed=True,
                is_trade_allowed=trade_allowed, trade_frozen=frozen,
                password_encrypted=credentials.encrypt_secret(password),
            )
            db.add(acct)
            await db.commit()
            return str(acct.id)

    return client.portal.call(_ins)


def _add_rule(client, account_id: str, *, rtype: str, threshold: float) -> None:
    async def _ins():
        from decimal import Decimal

        from app.engine.session import AsyncSessionLocal
        from app.models import RiskRule

        async with AsyncSessionLocal() as db:
            db.add(RiskRule(scope="account", account_id=uuid.UUID(account_id),
                            type=rtype, threshold=Decimal(str(threshold)),
                            action="reject"))
            await db.commit()

    client.portal.call(_ins)


def _audit_rows(client, action: str) -> list[tuple[str, dict]]:
    async def _q():
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(AuditLog.result, AuditLog.params, AuditLog.risk_decision)
                .where(AuditLog.action == action)
                .order_by(AuditLog.created_at)
            )).all()
            return [(r, p, d) for r, p, d in rows]

    return client.portal.call(_q)


def _risk_events(client, account_id: str) -> list[dict]:
    async def _q():
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import RiskEvent

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(RiskEvent).where(RiskEvent.account_id == uuid.UUID(account_id))
                .order_by(RiskEvent.created_at)
            )).scalars().all()
            return [{"rule_type": r.rule_type, "action_taken": r.action_taken} for r in rows]

    return client.portal.call(_q)


@pytest.fixture(autouse=True)
def _enable(client, monkeypatch):
    import app.services.manual_trade as mt_mod

    monkeypatch.setattr(mt_mod.settings, "MANUAL_TRADE_ENABLED", True)
    yield


def test_flag_off_409(client, token, stub, monkeypatch):
    import app.services.manual_trade as mt_mod

    acct_id = _seed_account(client, token)
    monkeypatch.setattr(mt_mod.settings, "MANUAL_TRADE_ENABLED", False)
    r = client.post(f"{API}/order",
                    json={"account_id": acct_id, "symbol": "EURUSD",
                          "side": "buy", "volume": 0.1},
                    headers=_auth(token))
    assert r.status_code == 409
    assert "未启用" in r.json()["detail"]


def test_foreign_account_404(client, token, stub):
    other = client.post(
        "/api/v1/auth/register",
        json={"email": f"mt2-{uuid.uuid4().hex[:8]}@test.com",
              "username": f"mt2{uuid.uuid4().hex[:8]}",
              "password": "Passw0rd!", "full_name": "越权"},
    ).json()["access_token"]
    r = client.get(f"{API}/accounts/{uuid.uuid4()}/snapshot",
                   headers=_auth(other))
    assert r.status_code == 404


def test_order_success_routes_to_node_and_audits(client, token, stub):
    acct_id = _seed_account(client, token, host="10.20.0.5", port=8205)
    r = client.post(f"{API}/order",
                    json={"account_id": acct_id, "symbol": "eurusd",
                          "side": "buy", "volume": 0.1},
                    headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["deal"] == 8002
    assert body["positions"] == []
    gw = stub["gw"]
    assert gw.base_url == "http://10.20.0.5:8205"  # M12 routing
    assert gw.orders_sent == [{"symbol": "EURUSD", "volume": 0.1, "side": "buy",
                               "magic": 0}]
    rows = _audit_rows(client, "manual_order")
    assert rows and rows[-1][0] == "success" and rows[-1][2] == "allow"


def test_order_blocked_by_lot_rule(client, token, stub):
    acct_id = _seed_account(client, token)
    _add_rule(client, acct_id, rtype="max_lot_per_order", threshold=0.05)
    r = client.post(f"{API}/order",
                    json={"account_id": acct_id, "symbol": "EURUSD",
                          "side": "buy", "volume": 0.1},
                    headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["error_code"] == "RISK_BLOCKED"
    assert stub["gw"].orders_sent == []  # 被拦截的单绝不下达网关
    rows = _audit_rows(client, "manual_order")
    assert rows[-1][0] == "blocked" and rows[-1][2] == "block"


def test_order_blocked_by_platform_lot_cap(client, token, stub):
    acct_id = _seed_account(client, token)
    r = client.post(f"{API}/order",
                    json={"account_id": acct_id, "symbol": "EURUSD",
                          "side": "buy", "volume": 50},
                    headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["success"] is False
    assert "平台上限" in (r.json()["error_message"] or "")
    assert stub["gw"].orders_sent == []


def test_order_count_failure_fails_closed(client, token, stub, monkeypatch):
    """2.4：限频计数取不到时必须拒绝，并且留下审计与风控事件。

    旧实现把查询失败折算成「0 单」——限频规则于是在数据库有状况时静默失效，
    正是它最该生效的时候。这里让计数函数直接返回 None（即查询失败的返回值）
    来复现，断言的是**整条链路**的失效方向，而不是某一个函数。
    """
    import app.services.manual_trade as mt_mod

    acct_id = _seed_account(client, token, host="10.20.0.9", port=8209)
    _add_rule(client, acct_id, rtype="max_orders_per_minute", threshold=20)
    monkeypatch.setattr(mt_mod, "_recent_manual_order_count", _async_none)

    r = client.post(f"{API}/order",
                    json={"account_id": acct_id, "symbol": "EURUSD",
                          "side": "buy", "volume": 0.1},
                    headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["error_code"] == "RISK_CHECK_UNAVAILABLE", body
    assert "无法" in body["error_message"]
    assert stub["gw"].orders_sent == [], "判定为不可信的单子绝不能到达券商"

    rows = _audit_rows(client, "manual_order")
    assert rows[-1][0] == "blocked" and rows[-1][2] == "block"

    events = _risk_events(client, acct_id)
    assert events, "fail-closed 拒绝必须留下风控事件（告警）"
    assert events[-1]["rule_type"] == "max_orders_per_minute"


async def _async_none(*_a, **_kw) -> None:
    return None


def test_readonly_account_rejected(client, token, stub):
    acct_id = _seed_account(client, token, trade_allowed=False)
    r = client.post(f"{API}/order",
                    json={"account_id": acct_id, "symbol": "EURUSD",
                          "side": "buy", "volume": 0.1},
                    headers=_auth(token))
    assert r.status_code == 409
    assert "禁止交易" in r.json()["detail"]


def test_frozen_account_blocks_close(client, token, stub):
    acct_id = _seed_account(client, token, frozen=True)
    r = client.post(f"{API}/close",
                    json={"account_id": acct_id, "ticket": 123},
                    headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["success"] is False
    assert r.json()["error_code"] == "RISK_BLOCKED"
    assert stub["gw"].closes == []
    rows = _audit_rows(client, "manual_close")
    assert rows[-1][0] == "blocked"


def test_close_success_reduces_risk_path(client, token, stub):
    acct_id = _seed_account(client, token)
    _add_rule(client, acct_id, rtype="max_lot_per_order", threshold=0.01)
    r = client.post(f"{API}/close",
                    json={"account_id": acct_id, "ticket": 123, "volume": 0.5},
                    headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["success"] is True  # 平仓是减仓，不受开仓手数闸限制
    assert stub["gw"].closes == [{"ticket": 123, "volume": 0.5, "magic": 0}]


def test_cancel_success(client, token, stub):
    acct_id = _seed_account(client, token)
    r = client.post(f"{API}/cancel",
                    json={"account_id": acct_id, "ticket": 501},
                    headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert stub["gw"].cancels == [501]


def test_frozen_account_blocks_cancel(client, token, stub):
    acct_id = _seed_account(client, token, frozen=True)
    r = client.post(f"{API}/cancel",
                    json={"account_id": acct_id, "ticket": 501},
                    headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["success"] is False
    assert stub["gw"].cancels == []


def test_broker_rejection_surfaces_comment(client, token, stub):
    """M14 regression: a broker-side reject (success=false, no platform error)
    must surface the gateway comment (e.g. "Market closed") instead of a bare
    empty message the frontend would render as "未知错误"."""
    acct_id = _seed_account(client, token)
    stub["reject"] = {"success": False, "retcode": 10018,
                      "retcode_text": "资金不足", "deal": 0, "order": 0,
                      "price": 0.0, "volume": 0.0, "comment": "Market closed"}
    r = client.post(f"{API}/order",
                    json={"account_id": acct_id, "symbol": "EURUSD",
                          "side": "buy", "volume": 0.1},
                    headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["error_message"] == "Market closed"
    assert body["retcode"] == 10018


def test_snapshot_returns_mirror(client, token, stub):
    acct_id = _seed_account(client, token, host="10.20.0.6", port=8206)
    r = client.get(f"{API}/accounts/{acct_id}/snapshot", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["account"]["equity"] == 10000.0
    assert body["orders"][0]["ticket"] == 501
    assert stub["gw"].base_url == "http://10.20.0.6:8206"


def test_login_failure_400(client, token, stub):
    acct_id = _seed_account(client, token, password="Good!")
    stub["fail_login"] = True
    r = client.get(f"{API}/accounts/{acct_id}/snapshot", headers=_auth(token))
    assert r.status_code == 400
    assert "登录绑定账户失败" in r.json()["detail"]
