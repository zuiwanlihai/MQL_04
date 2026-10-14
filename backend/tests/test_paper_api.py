"""Paper trading center API integration tests (M5).

The manager instantiates ``GatewayClient`` directly (no FastAPI DI), so the
gateway surface is monkeypatched at class level.  ``PAPER_ENABLED`` is disabled
for most tests so the fire-and-forget subprocess/poll loop never spawns — the
row stays ``pending`` and we assert the pure API/validation/control surface.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services.gateway_client import GatewayClient

API = "/api/v1/paper"
STRAT_API = "/api/v1/strategies"

POINT = 0.00001
TF_SECONDS = 3600
BASE_PRICE = 1.10000

SYMBOL_INFO = {
    "name": "EURUSD",
    "description": "Euro vs US Dollar",
    "digits": 5,
    "point": POINT,
    "spread": 20,
    "volume_step": 0.01,
    "volume_min": 0.01,
    "volume_max": 100.0,
    "trade_contract_size": 100000.0,
    "margin_initial": 0.0,
    "currency_base": "EUR",
    "currency_profit": "USD",
    "trade_mode": 4,
    "visible": True,
}

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


@pytest.fixture(autouse=True)
def gw(monkeypatch):
    async def fake_symbols(self) -> list[dict]:
        return [dict(SYMBOL_INFO)]

    async def fake_rates_range(self, name, timeframe, start, end, limit=5000):
        return []

    async def fake_rates(self, name, timeframe, limit):
        return []

    async def fake_aclose(self):
        return None

    monkeypatch.setattr(GatewayClient, "symbols", fake_symbols)
    monkeypatch.setattr(GatewayClient, "rates_range", fake_rates_range)
    monkeypatch.setattr(GatewayClient, "rates", fake_rates)
    monkeypatch.setattr(GatewayClient, "aclose", fake_aclose)
    yield


@pytest.fixture(autouse=True)
def _no_spawn(monkeypatch):
    """Keep rows pending: the manager never spawns subprocesses in these tests."""
    from app.api.v1.endpoints import paper as paper_mod

    monkeypatch.setattr(paper_mod.settings, "PAPER_ENABLED", False)
    yield


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def jwt_sub(client: TestClient, token: str) -> str:
    from jose import jwt

    from app.config import settings

    return jwt.decode(token, settings.JWT_SECRET_KEY,
                      algorithms=[settings.JWT_ALGORITHM])["sub"]


@pytest.fixture
def token(client: TestClient) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"pp-{uuid.uuid4().hex[:10]}@test.com",
            "username": f"pp{uuid.uuid4().hex[:10]}",
            "password": "Passw0rd!",
            "full_name": "模拟盘测试",
        },
    )
    assert r.status_code == 201
    return r.json()["access_token"]


@pytest.fixture
def strategy(client: TestClient, token: str) -> str:
    r = client.post(
        STRAT_API,
        json={"name": f"pp-{uuid.uuid4().hex[:8]}", "timeframe": "H1"},
        headers=_auth(token),
    )
    assert r.status_code == 201
    sid = r.json()["id"]
    src = client.put(
        f"{STRAT_API}/{sid}/source",
        json={"source_code": STRATEGY_SRC},
        headers=_auth(token),
    )
    assert src.status_code == 200, src.text
    return sid


@pytest.fixture
def account(client: TestClient, token: str) -> str:
    """Seed a gateway node + MT5 account owned by the test user (M12 routing)."""
    from app.services import credentials

    uid = jwt_sub(client, token)

    async def _ins() -> str:
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import GatewayNode, MT5Account

        async with AsyncSessionLocal() as db:
            node = (await db.execute(
                select(GatewayNode).where(GatewayNode.host == "127.0.0.1",
                                          GatewayNode.port == 8090)
            )).scalars().first()
            if node is None:
                node = GatewayNode(host="127.0.0.1", port=8090, status="online")
                db.add(node)
                await db.flush()
            acct = MT5Account(
                login=850000 + (uuid.uuid4().int % 90000),
                server="MetaQuotes-Demo", name="pp", account_type="demo",
                leverage=100, currency="USD", gateway_node_id=node.id,
                user_id=uuid.UUID(uid), live_allowed=True,
                password_encrypted=credentials.encrypt_secret("Passw0rd!"),
            )
            db.add(acct)
            await db.commit()
            return str(acct.id)

    return client.portal.call(_ins)


def _body(strategy_id: str, account_id: str, **kw) -> dict:
    base = {
        "strategy_id": strategy_id,
        "account_id": account_id,
        "version": None,
        "symbol": "EURUSD",
        "timeframe": "H1",
        "initial_deposit": 10000.0,
        "leverage": 100,
        "execution_mode": "standard",
        "spread_mode": "current",
        "warmup_bars": 100,
        "parameters": {},
        "assumption_ack": True,
    }
    base.update(kw)
    return base


# ── validation ─────────────────────────────────────────────────

def test_requires_auth(client):
    assert client.get(API).status_code == 401


def test_missing_assumption_ack_422(client, token, strategy, account):
    body = _body(strategy, account, assumption_ack=False)
    resp = client.post(API, json=body, headers=_auth(token))
    assert resp.status_code == 422


def test_fixed_spread_requires_points_422(client, token, strategy, account):
    body = _body(strategy, account, spread_mode="fixed", spread_points=None)
    resp = client.post(API, json=body, headers=_auth(token))
    assert resp.status_code == 422


def test_conservative_mode_rejected_422(client, token, strategy, account):
    # paper only accepts standard/optimistic tiers
    resp = client.post(
        API, json=_body(strategy, account, execution_mode="conservative"), headers=_auth(token)
    )
    assert resp.status_code == 422


def test_empty_source_422(client, token, account):
    sid = client.post(
        STRAT_API, json={"name": f"e-{uuid.uuid4().hex[:8]}"}, headers=_auth(token)
    ).json()["id"]
    resp = client.post(API, json=_body(sid, account), headers=_auth(token))
    assert resp.status_code == 422
    assert "代码为空" in resp.json()["detail"]


def test_unknown_strategy_404(client, token, account):
    resp = client.post(API, json=_body(str(uuid.uuid4()), account), headers=_auth(token))
    assert resp.status_code == 404


def test_unknown_account_404(client, token, strategy):
    resp = client.post(API, json=_body(strategy, str(uuid.uuid4())), headers=_auth(token))
    assert resp.status_code == 404
    assert "MT5 账户" in resp.json()["detail"]


def test_cross_user_account_404(client, token, strategy):
    """Another user's account → 404 (ownership model, same as live)."""
    other = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"pp3-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"pp3{uuid.uuid4().hex[:8]}",
            "password": "Passw0rd!",
            "full_name": "越权账户",
        },
    ).json()["access_token"]

    # seed an account owned by `other` directly
    from app.services import credentials

    uid = jwt_sub(client, other)

    async def _ins() -> str:
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import GatewayNode, MT5Account

        async with AsyncSessionLocal() as db:
            node = (await db.execute(
                select(GatewayNode).where(GatewayNode.host == "127.0.0.1",
                                          GatewayNode.port == 8090)
            )).scalars().first()
            if node is None:
                node = GatewayNode(host="127.0.0.1", port=8090, status="online")
                db.add(node)
                await db.flush()
            a = MT5Account(
                login=860000 + (uuid.uuid4().int % 90000),
                server="MetaQuotes-Demo", name="pp3", account_type="demo",
                leverage=100, currency="USD", gateway_node_id=node.id,
                user_id=uuid.UUID(uid),
                password_encrypted=credentials.encrypt_secret("Passw0rd!"),
            )
            db.add(a)
            await db.commit()
            return str(a.id)

    acct_id = client.portal.call(_ins)
    resp = client.post(API, json=_body(strategy, acct_id), headers=_auth(token))
    assert resp.status_code == 404


def test_archived_strategy_409(client, token, strategy, account):
    client.patch(f"{STRAT_API}/{strategy}", json={"status": "archived"}, headers=_auth(token))
    resp = client.post(API, json=_body(strategy, account), headers=_auth(token))
    assert resp.status_code == 409


# ── create / detail ────────────────────────────────────────────

def test_create_returns_run_with_frozen_snapshot(client, token, strategy, account):
    body = _body(strategy, account, execution_mode="optimistic")
    resp = client.post(API, json=body, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    run = resp.json()
    assert run["execution_mode"] == "optimistic"
    assert run["status"] == "pending"
    assert run["account_id"] == account
    assert run["balance"] == 10000.0
    assert run["equity"] == 10000.0
    detail = client.get(f"{API}/{run['id']}", headers=_auth(token)).json()
    snap = detail["config_snapshot"]
    assert snap["assumption_text"].startswith("乐观档")
    assert snap["assumption_ack"] is True
    assert snap["source_hash"] and snap["seed"]
    assert snap["sdk"]["python"]
    assert snap["account"]["id"] == account
    assert "positions" in detail and "equity_history" in detail


# ── concurrency cap ────────────────────────────────────────────

def test_concurrency_cap_409(client, token, strategy, account, monkeypatch):
    from app.api.v1.endpoints import paper as paper_mod

    monkeypatch.setattr(paper_mod.settings, "PAPER_MAX_CONCURRENT", 2)
    for _ in range(2):
        r = client.post(API, json=_body(strategy, account), headers=_auth(token))
        assert r.status_code == 201
    third = client.post(API, json=_body(strategy, account), headers=_auth(token))
    assert third.status_code == 409
    assert "上限" in third.json()["detail"]


# ── list / filter ──────────────────────────────────────────────

def test_list_filter_and_pagination(client, token, strategy, account):
    for _ in range(3):
        r = client.post(API, json=_body(strategy, account), headers=_auth(token))
        assert r.status_code == 201
    r = client.get(f"{API}?page=1&page_size=2", headers=_auth(token)).json()
    assert r["total"] >= 3
    assert len(r["items"]) == 2
    by_strategy = client.get(f"{API}?strategy_id={strategy}", headers=_auth(token)).json()
    assert all(i["strategy_id"] == strategy for i in by_strategy["items"])
    bad = client.get(f"{API}?status=zzz", headers=_auth(token))
    assert bad.status_code == 422


def test_cross_user_access_404(client, token, strategy, account):
    rid = client.post(API, json=_body(strategy, account), headers=_auth(token)).json()["id"]
    other = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"pp2-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"pp2{uuid.uuid4().hex[:8]}",
            "password": "Passw0rd!",
            "full_name": "越权测试",
        },
    ).json()["access_token"]
    assert client.get(f"{API}/{rid}", headers=_auth(other)).status_code == 404
    assert client.get(f"{API}/{rid}/trades", headers=_auth(other)).status_code == 404
    assert client.get(f"{API}/{rid}/logs", headers=_auth(other)).status_code == 404
    assert client.post(f"{API}/{rid}/pause", headers=_auth(other)).status_code == 404


# ── control on pending (no live process) ───────────────────────

def test_pause_pending_409(client, token, strategy, account):
    rid = client.post(API, json=_body(strategy, account), headers=_auth(token)).json()["id"]
    assert client.post(f"{API}/{rid}/pause", headers=_auth(token)).status_code == 409


def test_stop_pending_marks_failed(client, token, strategy, account):
    rid = client.post(API, json=_body(strategy, account), headers=_auth(token)).json()["id"]
    r = client.post(f"{API}/{rid}/stop", json={"close_positions": True}, headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    assert r.json()["error_code"] == "CANCELLED"


def test_reset_pending_409(client, token, strategy, account):
    rid = client.post(API, json=_body(strategy, account), headers=_auth(token)).json()["id"]
    assert client.post(f"{API}/{rid}/reset", headers=_auth(token)).status_code == 409


# ── 成交落库（2.3 超时后的成交泄漏） ────────────────────────────

def _trade_event(ticket: int, **kw) -> dict:
    ev = {
        "event": "trade",
        "ticket": ticket,
        "direction": "buy",
        "volume": 0.1,
        "open_time": 1_760_000_400,
        "open_price": 1.1,
        "base_price": 1.1,
        "close_time": 1_760_004_000,
        "close_price": 1.1005,
        "profit": 5.0,
        "profit_percent": 0.5,
        "is_forced": False,
    }
    ev.update(kw)
    return ev


def test_suspect_flag_from_the_engine_reaches_the_row(client, token, strategy, account):
    """引擎标出的可疑成交必须一路落到 ``paper_trades.suspect``。

    超时回调里的成交是先于失败判定落库的（``_stream`` 里 ``_flush_rows`` 在失败
    分支之前），所以这条标记只能在事件上传递——丢了它就等于没标。
    """
    rid = client.post(API, json=_body(strategy, account),
                      headers=_auth(token)).json()["id"]

    async def _run() -> list[tuple[int, bool]]:
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import PaperTrade
        from app.services.paper.manager import PaperManager, _RunHandle

        mgr = PaperManager()
        mgr._handles[rid] = _RunHandle(
            run_id=rid, sup=object(), stream_task=None, poll_task=None,
            dir=Path("."), symbol="EURUSD", timeframe="H1", tf_seconds=TF_SECONDS,
        )
        on_event = mgr._on_event(rid)
        await on_event(_trade_event(1, suspect=True,
                                    suspect_reason="on_bar() 超出回调时限后被终止"))
        await on_event(_trade_event(2))  # 正常成交：不得被连坐
        await mgr._flush_rows(rid)

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(PaperTrade).where(PaperTrade.run_id == uuid.UUID(rid))
                .order_by(PaperTrade.ticket)
            )).scalars().all()
            return [(r.ticket, r.suspect) for r in rows]

    assert client.portal.call(_run) == [(1, True), (2, False)]
