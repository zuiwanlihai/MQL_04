"""Reconcile / demotion / alerts / rules CRUD tests (M6 Phase E)."""

from __future__ import annotations

import uuid

import pytest

API = "/api/v1/live"


class StubGW:
    """Positions stub keyed per call."""

    def __init__(self):
        self.positions_rows: list[dict] = []

    async def positions(self, symbol=None, timeout=None):
        return list(self.positions_rows)

    async def account(self, timeout=None):
        return None

    async def aclose(self):
        pass


@pytest.fixture
def stub() -> StubGW:
    stub = StubGW()
    import app.services.live.reconcile as rec_mod

    orig = rec_mod.GatewayClient

    def _make(*a, **k):
        return stub

    # 被测代码走 GatewayClient.for_registered_node(...)：替代品要能应答，
    # 否则用例会红在「接口不匹配」上，而不是红在真正要验的对账逻辑上。
    _make.for_registered_node = _make
    rec_mod.GatewayClient = _make
    yield stub
    rec_mod.GatewayClient = orig


@pytest.fixture
def token(client) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"rc-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"rc{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "对账测试"},
    )
    return r.json()["access_token"]


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _seed_run(client, token, *, positions: list[dict], magic: int,
              status: str = "running") -> dict:
    """Insert gateway node + account + live run directly on the app loop."""

    async def _ins():
        from decimal import Decimal

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
            strat = Strategy(user_id=uid, name=f"rc-{uuid.uuid4().hex[:8]}",
                             timeframe="H1", status="active")
            db.add(strat)
            acct = MT5Account(
                login=810000 + (uuid.uuid4().int % 9000),
                server="MetaQuotes-Demo", name="t", account_type="demo",
                leverage=100, currency="USD", gateway_node_id=node.id,
                user_id=uid, live_allowed=True,
            )
            db.add(acct)
            await db.flush()
            run = LiveRun(
                user_id=uid,
                strategy_id=strat.id, account_id=acct.id,
                source_code="x", symbol="EURUSD", timeframe="H1",
                execution_mode="standard",
                allocated_capital=Decimal("1000"), leverage=100,
                magic=magic, positions=positions, status=status,
                balance=Decimal("1000"), equity=Decimal("1000"),
                margin_free=Decimal("1000"),
            )
            db.add(run)
            await db.commit()
            return {"run_id": str(run.id), "account_id": str(acct.id)}
    return client.portal.call(_ins)


def _uid(client, token) -> str:
    from jose import jwt

    from app.config import settings

    return jwt.decode(token, settings.JWT_SECRET_KEY,
                      algorithms=[settings.JWT_ALGORITHM])["sub"]


def _promote(client, token, role: str = "admin") -> None:
    """把 token 对应的用户提成 role。

    本文件这两个用例在验「平台的」风控/告警：global 规则改的是全平台默认，平台
    级告警（如 kill_switch）没有归属人。2.7 之后两者都归管理员，所以这里显式
    提权——不提的话，跑不跑得通取决于这个用户是不是库里第一个注册的。
    """
    from sqlalchemy import update

    from app.engine.session import AsyncSessionLocal
    from app.models import User

    uid = uuid.UUID(_uid(client, token))

    async def _up() -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(update(User).where(User.id == uid).values(role=role))
            await db.commit()
    client.portal.call(_up)


# ── reconcile ──────────────────────────────────────────────────

def test_reconcile_mismatch_freezes_account(client, token, stub):
    from app.services.live.reconcile import reconcile_once

    seeded = _seed_run(client, token, positions=[
        {"ticket": 101, "symbol": "EURUSD", "type": "buy", "volume": 0.1,
         "price_open": 1.1, "price_current": 1.1, "profit": 0.0, "swap": 0.0,
         "commission": 0.0, "time": 1, "magic": 777, "identifier": 101,
         "comment": ""}], magic=777)
    # gateway shows a DIFFERENT set (ticket 999 exists there, 101 gone)
    stub.positions_rows = [
        {"ticket": 999, "symbol": "EURUSD", "type": "sell", "volume": 0.1,
         "price_open": 1.1, "price_current": 1.1, "profit": 0.0, "swap": 0.0,
         "commission": 0.0, "time": 1, "magic": 777, "identifier": 999,
         "comment": ""}]
    out = client.portal.call(reconcile_once)
    assert out[seeded["account_id"]] == 2  # symmetric diff {101,999}

    async def _check():
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AlertEvent, MT5Account, RiskEvent

        async with AsyncSessionLocal() as db:
            acct = await db.get(MT5Account, uuid.UUID(seeded["account_id"]))
            alerts = (await db.execute(
                select(AlertEvent).where(AlertEvent.source == "reconcile")
            )).scalars().all()
            events = (await db.execute(
                select(RiskEvent).where(RiskEvent.rule_type == "reconcile_mismatch")
            )).scalars().all()
            return acct.trade_frozen, len(alerts), len(events)
    frozen, n_alerts, n_events = client.portal.call(_check)
    assert frozen is True
    assert n_alerts >= 1
    assert n_events >= 1


def test_reconcile_match_converges_pending(client, token, stub):
    from app.services.live.reconcile import reconcile_once

    pos = [{"ticket": 202, "symbol": "EURUSD", "type": "buy", "volume": 0.1,
            "price_open": 1.1, "price_current": 1.1, "profit": 0.0, "swap": 0.0,
            "commission": 0.0, "time": 1, "magic": 888, "identifier": 202,
            "comment": ""}]
    seeded = _seed_run(client, token, positions=pos, magic=888)
    stub.positions_rows = pos

    async def _flag():
        from app.engine.session import AsyncSessionLocal
        from app.models import LiveRun

        async with AsyncSessionLocal() as db:
            run = await db.get(LiveRun, uuid.UUID(seeded["run_id"]))
            run.reconcile_pending = True
            db.add(run)
            await db.commit()
    client.portal.call(_flag)

    out = client.portal.call(reconcile_once)
    assert out[seeded["account_id"]] == 0

    async def _check():
        from app.engine.session import AsyncSessionLocal
        from app.models import LiveRun

        async with AsyncSessionLocal() as db:
            run = await db.get(LiveRun, uuid.UUID(seeded["run_id"]))
            return run.reconcile_pending
    assert client.portal.call(_check) is False


# ── demotion ───────────────────────────────────────────────────

def test_close_all_and_stop_demotes(client, token, stub):
    from app.services.live.manager import get_live_manager

    seeded = _seed_run(client, token, positions=[], magic=999)
    mgr = get_live_manager()

    async def _demote():
        await mgr._demote(seeded["run_id"], reason="测试降级")
    client.portal.call(_demote)

    async def _check():
        from app.engine.session import AsyncSessionLocal
        from app.models import LiveRun, MT5Account

        async with AsyncSessionLocal() as db:
            run = await db.get(LiveRun, uuid.UUID(seeded["run_id"]))
            acct = await db.get(MT5Account, run.account_id)
            return run.demoted, acct.live_allowed
    demoted, live_allowed = client.portal.call(_check)
    assert demoted is True
    assert live_allowed is False


# ── alerts + unfreeze + rules CRUD ─────────────────────────────

def test_alerts_list_and_read(client, token, stub):
    from app.services.alert import emit as alert_emit

    # kill_switch 是平台级告警（无归属账户/运行）：已读归管理员（2.7）。
    _promote(client, token)

    async def _emit():
        return await alert_emit("kill_switch", "测试告警", "内容", level="error")
    row = client.portal.call(_emit)
    alert_id = str(row.id)

    r = client.get(f"{API}/alerts?unread_only=true", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["unread"] >= 1
    assert any(i["id"] == alert_id for i in body["items"])

    r = client.post(f"{API}/alerts/{alert_id}/read", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["is_read"] is True


def test_unfreeze_requires_admin_confirm(client, token, stub):
    seeded = _seed_run(client, token, positions=[], magic=555)

    async def _freeze():
        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account

        async with AsyncSessionLocal() as db:
            acct = await db.get(MT5Account, uuid.UUID(seeded["account_id"]))
            acct.trade_frozen = True
            db.add(acct)
            await db.commit()
    client.portal.call(_freeze)

    bad = client.post(f"{API}/accounts/{seeded['account_id']}/unfreeze",
                      json={"review_note": "核对无误，差异为手动平仓",
                            "confirm": "WRONG"}, headers=_auth(token))
    assert bad.status_code == 422

    role = client.get("/api/v1/auth/me", headers=_auth(token)).json()["role"]
    ok = client.post(f"{API}/accounts/{seeded['account_id']}/unfreeze",
                     json={"review_note": "核对无误，差异为手动平仓",
                           "confirm": "UNFREEZE"}, headers=_auth(token))
    # admin (first registered user) → 200; trader → 403
    assert ok.status_code == (200 if role == "admin" else 403)


def test_rules_crud_and_audit(client, token):
    # 重点是 CRUD + 审计留痕，不是「谁能建全局规则」；scope=global 只是省掉建账户。
    # global 归管理员（2.7），所以这里提权，让用例验的仍是它自己那件事。
    _promote(client, token)
    r = client.post(f"{API}/rules", json={
        "scope": "global", "type": "max_lot_per_order", "threshold": 0.5,
        "action": "reject", "note": "全局单笔上限"}, headers=_auth(token))
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["type"] == "max_lot_per_order"

    r = client.patch(f"{API}/rules/{rule['id']}",
                     json={"threshold": 0.3}, headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["threshold"] == 0.3

    r = client.get(f"{API}/rules", headers=_auth(token))
    assert r.status_code == 200
    assert any(x["id"] == rule["id"] for x in r.json())

    r = client.delete(f"{API}/rules/{rule['id']}", headers=_auth(token))
    assert r.status_code == 204

    async def _audit():
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(AuditLog).where(AuditLog.action == "rule_change",
                                       AuditLog.target_id == rule["id"])
            )).scalars().all()
            return [r0.params.get("op") for r0 in rows]
    ops = client.portal.call(_audit)
    assert set(ops) == {"create", "update", "delete"}
