"""Alert-rule engine tests (M15, PRD M7 告警规则 P0).

CRUD + ownership + validation over the API; evaluation semantics
(drawdown / daily loss / gateway offline / trade cursor / cooldown) via
``evaluate_once`` against seeded mirrors.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

API = "/api/v1/alert-rules/rules"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _token(client) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"ar-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"ar{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "规则测试"},
    )
    assert r.status_code == 201
    return r.json()["access_token"]


def _uid(client, token) -> str:
    from jose import jwt

    from app.config import settings

    return jwt.decode(token, settings.JWT_SECRET_KEY,
                      algorithms=[settings.JWT_ALGORITHM])["sub"]


def _create(client, token, **over):
    body = {"name": "回撤10%", "type": "drawdown_pct", "scope": "global",
            "threshold": 10, "level": "warn", "cooldown_seconds": 0}
    body.update(over)
    return client.post(API, json=body, headers=_auth(token))


# ── CRUD / validation / ownership ─────────────────────────────

def test_rule_crud_roundtrip(client):
    token = _token(client)
    r = _create(client, token)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["type"] == "drawdown_pct" and body["threshold"] == 10.0
    assert body["enabled"] is True

    lst = client.get(API, headers=_auth(token)).json()
    assert len(lst) == 1

    upd = client.patch(f"{API}/{body['id']}",
                       json={"threshold": 20, "enabled": False},
                       headers=_auth(token))
    assert upd.status_code == 200
    assert upd.json()["threshold"] == 20.0 and upd.json()["enabled"] is False

    assert client.delete(f"{API}/{body['id']}", headers=_auth(token)).status_code == 204
    assert client.get(API, headers=_auth(token)).json() == []


def test_rule_validation_rules(client):
    token = _token(client)
    # percent rules need threshold > 0
    assert _create(client, token, threshold=0).status_code == 422
    # gateway_offline floor of 5s
    assert _create(client, token, type="gateway_offline", threshold=2).status_code == 422
    # custom_metric requires metric
    assert _create(client, token, type="custom_metric").status_code == 422
    # account scope requires account_id
    assert _create(client, token, scope="account").status_code == 422
    # unknown account id → 404
    assert _create(client, token, scope="account",
                   account_id=str(uuid.uuid4())).status_code == 404
    # unknown metric literal → 422 (Literal)
    assert _create(client, token, type="custom_metric",
                   metric="margin_level").status_code == 422


def test_rule_isolation_between_users(client):
    t1, t2 = _token(client), _token(client)
    rid = _create(client, t1).json()["id"]
    assert client.get(API, headers=_auth(t2)).json() == []
    assert client.patch(f"{API}/{rid}", json={"enabled": False},
                        headers=_auth(t2)).status_code == 404
    assert client.delete(f"{API}/{rid}", headers=_auth(t2)).status_code == 404


# ── evaluation: seeding helpers ───────────────────────────────

def _seed_live_run(client, token, *, equity, peak, day_start,
                   day_start_date="2026-10-11", status="running") -> dict:
    """Gateway node + account + strategy + live run mirror with given numbers."""

    async def _ins():
        from sqlalchemy import select as _select

        from app.engine.session import AsyncSessionLocal
        from app.models import GatewayNode, LiveRun, MT5Account, Strategy

        uid = uuid.UUID(_uid(client, token))
        async with AsyncSessionLocal() as db:
            node = (await db.execute(_select(GatewayNode).where(
                GatewayNode.host == "127.0.0.1", GatewayNode.port == 8090
            ))).scalars().first()
            if node is None:
                node = GatewayNode(host="127.0.0.1", port=8090, status="online")
                db.add(node)
                await db.flush()
            strat = Strategy(user_id=uid, name=f"ar-{uuid.uuid4().hex[:8]}",
                             timeframe="H1", status="active")
            db.add(strat)
            acct = MT5Account(
                login=820000 + (uuid.uuid4().int % 9000),
                server="MetaQuotes-Demo", name="t", account_type="demo",
                leverage=100, currency="USD", gateway_node_id=node.id,
                user_id=uid, live_allowed=True,
            )
            db.add(acct)
            await db.flush()
            run = LiveRun(
                user_id=uid, strategy_id=strat.id, account_id=acct.id,
                source_code="x", symbol="EURUSD", timeframe="H1",
                execution_mode="standard", allocated_capital=Decimal("1000"),
                leverage=100, magic=int(uuid.uuid4().int % 100000),
                status=status, balance=Decimal(str(equity)),
                equity=Decimal(str(equity)), peak_equity=Decimal(str(peak)),
                day_start_equity=Decimal(str(day_start)),
                day_start_date=day_start_date, margin_free=Decimal(str(equity)),
            )
            db.add(run)
            await db.commit()
            return {"run_id": str(run.id), "account_id": str(acct.id)}
    return client.portal.call(_ins)


def _seed_trades(client, token, run_id: str, tickets: list[int]) -> None:
    async def _ins():
        from datetime import datetime, timezone

        from app.engine.session import AsyncSessionLocal
        from app.models import LiveTrade

        async with AsyncSessionLocal() as db:
            for t in tickets:
                db.add(LiveTrade(
                    run_id=uuid.UUID(run_id), deal_ticket=t, order_ticket=t,
                    direction="buy", volume=Decimal("0.1"),
                    open_time=datetime.now(timezone.utc),
                    price=Decimal("1.1"),
                ))
            await db.commit()
    client.portal.call(_ins)


def _count_alerts(client, source: str = "alert_rule") -> int:
    async def _q():
        from sqlalchemy import func, select

        from app.engine.session import AsyncSessionLocal
        from app.models import AlertEvent

        async with AsyncSessionLocal() as db:
            return (await db.execute(
                select(func.count()).select_from(AlertEvent)
                .where(AlertEvent.source == source)
            )).scalar_one()
    return client.portal.call(_q)


def _cleanup(client, token) -> None:
    """Delete every rule of this user so later tests see a quiet evaluator."""
    import app.services.alert_rules as ar

    for r in client.get(API, headers=_auth(token)).json():
        client.delete(f"{API}/{r['id']}", headers=_auth(token))
    ar.reset_state()


# ── evaluation semantics ──────────────────────────────────────

def test_drawdown_fires_with_cooldown_suppression(client, monkeypatch):
    import app.services.alert_rules as ar

    ar.reset_state()
    token = _token(client)
    seeded = _seed_live_run(client, token, equity=80, peak=100, day_start=100)

    rid = _create(client, token, threshold=15, cooldown_seconds=3600).json()["id"]

    fired = client.portal.call(ar.evaluate_once)
    assert fired == 1
    assert _count_alerts(client) == 1

    # still breached, but inside cooldown → suppressed
    assert client.portal.call(ar.evaluate_once) == 0
    assert _count_alerts(client) == 1

    # re-tuned rule clears its own cooldown → fires again
    client.patch(f"{API}/{rid}", json={"threshold": 10}, headers=_auth(token))
    assert client.portal.call(ar.evaluate_once) == 1

    # disabled rule never fires
    client.patch(f"{API}/{rid}", json={"enabled": False}, headers=_auth(token))
    assert client.portal.call(ar.evaluate_once) == 0
    _cleanup(client, token)
    assert seeded["run_id"]


def test_daily_loss_and_custom_metric(client):
    import app.services.alert_rules as ar

    ar.reset_state()
    token = _token(client)
    _seed_live_run(client, token, equity=88, peak=100, day_start=100)

    # drawdown 12% < 20 threshold → no drawdown alert
    _create(client, token, name="回撤20", type="drawdown_pct", threshold=20)
    # daily loss 12% ≥ 10 → fires
    _create(client, token, name="日亏10", type="daily_loss_pct", threshold=10)
    # equity=88 < 90 → fires
    _create(client, token, name="权益低于90", type="custom_metric",
            metric="equity", operator="lt", threshold=90)

    fired = client.portal.call(ar.evaluate_once)
    assert fired == 2
    _cleanup(client, token)


def test_gateway_offline_threshold_and_recovery(client):
    import app.services.alert_rules as ar

    ar.reset_state()
    token = _token(client)
    _create(client, token, name="断线60s", type="gateway_offline", threshold=60)

    async def _set_status(status: str):
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import GatewayNode

        async with AsyncSessionLocal() as db:
            node = (await db.execute(
                select(GatewayNode).where(GatewayNode.host == "127.0.0.1",
                                          GatewayNode.port == 8090)
            )).scalars().first()
            if node is None:
                node = GatewayNode(host="127.0.0.1", port=8090, status=status)
                db.add(node)
            else:
                node.status = status
            await db.commit()
            return str(node.id)

    nid = client.portal.call(lambda: _set_status("offline"))
    # first sight: offline_since set, but 0s < 60s → no alert
    assert client.portal.call(ar.evaluate_once) == 0
    # backdate the offline clock
    ar._offline_since[nid] -= 120
    assert client.portal.call(ar.evaluate_once) == 1
    # cooldown 0 → would refire while offline
    assert client.portal.call(ar.evaluate_once) == 1
    # recovered → clock cleared, no further alerts
    client.portal.call(lambda: _set_status("online"))
    assert client.portal.call(ar.evaluate_once) == 0
    assert nid not in ar._offline_since
    ar.reset_state()


def test_trade_filled_cursor_seeding(client):
    import app.services.alert_rules as ar

    ar.reset_state()
    token = _token(client)
    seeded = _seed_live_run(client, token, equity=100, peak=100, day_start=100)
    _seed_trades(client, token, seeded["run_id"], [1, 2, 3])

    _create(client, token, name="成交告警", type="trade_filled", threshold=0)

    # first pass seeds the cursor — historical fills must not flood alerts
    assert client.portal.call(ar.evaluate_once) == 0
    # new deal appears → one alert
    _seed_trades(client, token, seeded["run_id"], [4])
    assert client.portal.call(ar.evaluate_once) == 1
    # no new deals → silent
    assert client.portal.call(ar.evaluate_once) == 0
    ar.reset_state()
