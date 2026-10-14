"""Dashboard aggregation API tests (M7 Phase A)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

API = "/api/v1/dashboard"
STRAT_API = "/api/v1/strategies"
ACCT_API = "/api/v1/accounts"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class StubGateway:
    async def login(self, login, password, server):
        return {
            "login": login, "server": server, "name": "Dash Test",
            "type": "demo", "leverage": 100, "currency": "USD",
            "balance": 1000.0, "credit": 0.0, "equity": 1000.0, "margin": 0.0,
            "margin_free": 1000.0, "margin_level": 0.0, "trade_allowed": True,
        }

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def _gw(monkeypatch):
    from app.main import app
    from app.services.gateway_client import get_gateway_client

    app.dependency_overrides[get_gateway_client] = lambda: StubGateway()
    yield
    app.dependency_overrides.pop(get_gateway_client, None)


@pytest.fixture
def token(client) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"db-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"db{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "仪表盘测试"},
    )
    assert r.status_code == 201
    return r.json()["access_token"]


def _seed_live_run(client, uid: str, sid: str, acct_id: str, *,
                   status: str = "running", equity: float = 1050.0,
                   day_start: float = 1000.0, unrealized: float = 50.0,
                   demoted: bool = False) -> str:
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    async def _ins():
        from decimal import Decimal

        from app.engine.session import AsyncSessionLocal
        from app.models import LiveRun

        async with AsyncSessionLocal() as db:
            db.add(LiveRun(
                id=uuid.UUID(run_id), user_id=uuid.UUID(uid),
                strategy_id=uuid.UUID(sid), account_id=uuid.UUID(acct_id),
                source_code="def on_init(ctx): pass",
                strategy_version=1, symbol="EURUSD", timeframe="H1",
                allocated_capital=Decimal("1000"), leverage=100,
                magic=90000001, status=status,
                balance=Decimal(str(equity - unrealized)),
                equity=Decimal(str(equity)),
                unrealized_pnl=Decimal(str(unrealized)),
                day_start_equity=Decimal(str(day_start)),
                day_start_date=now.strftime("%Y-%m-%d"),
                demoted=demoted,
                positions=[{"ticket": 123, "symbol": "EURUSD", "type": "buy",
                            "volume": 0.1, "price_open": 1.1, "price_current": 1.11,
                            "profit": 50.0, "swap": 0.0, "time": 0, "magic": 90000001}],
                equity_history=[
                    {"time": now.isoformat(), "equity": equity, "balance": equity - unrealized},
                    {"time": "2000-01-01T00:00:00+00:00", "equity": 1.0, "balance": 1.0},
                ],
            ))
            await db.commit()
    client.portal.call(_ins)
    return run_id


def _seed_alert(client, *, level: str = "warn", title: str = "测试告警") -> None:
    async def _ins():
        from app.engine.session import AsyncSessionLocal
        from app.models import AlertEvent

        async with AsyncSessionLocal() as db:
            db.add(AlertEvent(level=level, source="risk", title=title, message="m"))
            await db.commit()
    client.portal.call(_ins)


def _own_ids(client, token: str) -> dict:
    sid = client.post(STRAT_API, json={"name": f"dash-{uuid.uuid4().hex[:6]}",
                       "timeframe": "H1"}, headers=_auth(token)).json()["id"]
    login = 800000 + (uuid.uuid4().int % 90000)
    acct = client.post(ACCT_API, json={"login": login, "password": "x",
                       "server": "MetaQuotes-Demo"}, headers=_auth(token))
    assert acct.status_code == 201, acct.text
    me = client.get("/api/v1/auth/me", headers=_auth(token)).json()
    return {"strategy_id": sid, "account_id": acct.json()["id"], "user_id": me["id"]}


def test_dashboard_empty(client, token):
    r = client.get(f"{API}/overview", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["accounts"] == []
    assert body["totals"]["equity"] == 0.0
    assert body["live_runs"] == []
    assert body["equity_curve"] == []


def test_dashboard_aggregation(client, token):
    ids = _own_ids(client, token)
    _seed_live_run(client, ids["user_id"], ids["strategy_id"], ids["account_id"])
    _seed_alert(client, title=f"dash-{uuid.uuid4().hex[:6]}")

    r = client.get(f"{API}/overview", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()

    assert len(body["accounts"]) == 1
    assert body["accounts"][0]["equity"] == pytest.approx(1000.0)
    # headline equity = real account mirror
    assert body["totals"]["equity"] == pytest.approx(1000.0)
    # today pnl from the run: 1050 - 1000
    assert body["totals"]["today_pnl"] == pytest.approx(50.0)
    assert body["totals"]["unrealized_pnl"] == pytest.approx(50.0)
    assert body["totals"]["running_live"] == 1

    assert len(body["live_runs"]) == 1
    card = body["live_runs"][0]
    assert card["status"] == "running"
    assert card["strategy_name"].startswith("dash-")
    assert card["today_pnl"] == pytest.approx(50.0)

    assert len(body["positions"]) == 1
    assert body["positions"][0]["ticket"] == 123

    # only today's point survives (year 2000 filtered out)
    assert len(body["equity_curve"]) == 1
    assert body["equity_curve"][0]["equity"] == pytest.approx(1050.0)

    assert body["alerts_unread"] >= 1
    assert any(a["title"].startswith("dash-") for a in body["alerts"])


def test_dashboard_excludes_other_users(client, token):
    ids = _own_ids(client, token)
    _seed_live_run(client, ids["user_id"], ids["strategy_id"], ids["account_id"])

    other = client.post(
        "/api/v1/auth/register",
        json={"email": f"db2-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"db2{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "他人"},
    ).json()["access_token"]
    r = client.get(f"{API}/overview", headers=_auth(other))
    assert r.json()["live_runs"] == []
    assert r.json()["accounts"] == []


def test_dashboard_demoted_and_paused_counts(client, token):
    ids = _own_ids(client, token)
    _seed_live_run(client, ids["user_id"], ids["strategy_id"], ids["account_id"],
                   status="paused")
    _seed_live_run(client, ids["user_id"], ids["strategy_id"], ids["account_id"],
                   status="running", demoted=True)

    body = client.get(f"{API}/overview", headers=_auth(token)).json()
    assert body["totals"]["running_live"] == 1
    assert body["totals"]["demoted_live"] == 1
    assert len(body["live_runs"]) == 2
