"""Live admission four-gate + deploy API tests (M6 Phase D).

The gateway surface is stubbed (bind flow), and the pre-conditions for the
backtest / paper-duration gates are seeded directly through the TestClient's
blocking portal so they land on the app's event loop.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

API = "/api/v1/live"
STRAT_API = "/api/v1/strategies"
ACCT_API = "/api/v1/accounts"

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


class StubGateway:
    async def login(self, login, password, server):
        return {
            "login": login, "server": server, "name": "Live Test",
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
    from app.api.v1.endpoints import live as live_mod

    monkeypatch.setattr(live_mod.settings, "LIVE_ENABLED", True)
    # never spawn real subprocesses in these API tests
    from app.services.live.manager import get_live_manager

    mgr = get_live_manager()
    monkeypatch.setattr(mgr, "spawn_start", lambda run_id: None)
    yield
    app.dependency_overrides.pop(get_gateway_client, None)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(client) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"lv-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"lv{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "实盘测试"},
    )
    assert r.status_code == 201
    return r.json()["access_token"]


@pytest.fixture
def seeded(client, token):
    """Create strategy(v1) + bound account; return ids + login tail."""
    sid = client.post(
        STRAT_API, json={"name": f"lv-{uuid.uuid4().hex[:8]}", "timeframe": "H1"},
        headers=_auth(token),
    ).json()["id"]
    client.put(f"{STRAT_API}/{sid}/source", json={"source_code": STRATEGY_SRC},
               headers=_auth(token))
    ver = client.post(f"{STRAT_API}/{sid}/versions",
                      json={"changelog": "v1"}, headers=_auth(token))
    assert ver.status_code == 201, ver.text
    login = 700000 + (uuid.uuid4().int % 90000)
    acct = client.post(ACCT_API, json={"login": login, "password": "x",
                       "server": "MetaQuotes-Demo"}, headers=_auth(token))
    assert acct.status_code == 201, acct.text
    return {"strategy_id": sid, "version": ver.json()["version"],
            "account_id": acct.json()["id"], "login": login}


def _mark_live_allowed(client, account_id: str) -> None:
    async def _set():
        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account

        async with AsyncSessionLocal() as db:
            acct = await db.get(MT5Account, uuid.UUID(account_id))
            acct.live_allowed = True
            # 实盘/模拟互斥：权限闸要求绑定行是实盘账户
            acct.account_type = "real"
            db.add(acct)
            await db.commit()
    client.portal.call(_set)


def _seed_completed_backtest(client, seeded) -> None:
    from decimal import Decimal

    async def _ins():
        from app.engine.session import AsyncSessionLocal
        from app.models import BacktestReport, BacktestTask

        async with AsyncSessionLocal() as db:
            task = BacktestTask(
                user_id=uuid.UUID(seeded["uid"]),
                strategy_id=uuid.UUID(seeded["strategy_id"]),
                source_code=STRATEGY_SRC, execution_mode="standard",
                symbol="EURUSD", timeframe="H1",
                start_date=datetime.now(UTC) - timedelta(days=30),
                end_date=datetime.now(UTC),
                initial_deposit=Decimal("10000"), leverage=100,
                strategy_version=seeded["version"], status="completed",
                completed_at=datetime.now(UTC),
            )
            db.add(task)
            await db.flush()
            db.add(BacktestReport(
                task_id=task.id, total_return=Decimal("100"),
                total_return_percent=Decimal("1"),
                annual_return_percent=Decimal("12"),
                annual_volatility_percent=Decimal("5"),
                sharpe_ratio=Decimal("1.2"), max_drawdown_percent=Decimal("8"),
                total_trades=10, win_rate=Decimal("0.6"),
            ))
            await db.commit()
    client.portal.call(_ins)


def _seed_paper_attest(client, seeded, days: int) -> None:
    async def _ins():
        from app.engine.session import AsyncSessionLocal
        from app.models import PaperDailyStats

        async with AsyncSessionLocal() as db:
            today = datetime.now(UTC).date()
            for i in range(1, days + 1):
                db.add(PaperDailyStats(
                    strategy_id=uuid.UUID(seeded["strategy_id"]),
                    strategy_version=seeded["version"],
                    account_id=None, day=today - timedelta(days=i),
                    uptime_seconds=86000, bars_processed=280,
                    risk_triggers=0, sandbox_violations=0,
                ))
            await db.commit()
    client.portal.call(_ins)


def _create_body(seeded, **kw) -> dict:
    tail = str(seeded["login"])[-4:]
    base = {
        "strategy_id": seeded["strategy_id"], "version": seeded["version"],
        "account_id": seeded["account_id"], "symbol": "EURUSD",
        "timeframe": "H1", "allocated_capital": 500.0, "leverage": 100,
        "warmup_bars": 100, "parameters": {},
        "rules": [{"type": "daily_max_loss", "threshold": 50.0,
                   "action": "halt_open_today"},
                  {"type": "max_drawdown", "threshold": 20.0,
                   "action": "close_all_and_stop"}],
        "disclaimer_ack": True, "account_tail": tail,
    }
    base.update(kw)
    return base


# ── admission gates ────────────────────────────────────────────

def test_admission_all_fail_without_prereqs(client, token, seeded):
    seeded["uid"] = _uid_of(client, token)
    r = client.post(f"{API}/wizard/check-admission", json={
        "strategy_id": seeded["strategy_id"], "version": seeded["version"],
        "account_id": seeded["account_id"]}, headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["allowed"] is False
    gates = {g["key"]: g["passed"] for g in body["gates"]}
    assert gates["permission"] is False      # live_allowed not set
    assert gates["backtest"] is False        # no completed backtest
    assert gates["paper_duration"] is False  # no attest days


def test_admission_all_pass(client, token, seeded):
    seeded["uid"] = _uid_of(client, token)
    _mark_live_allowed(client, seeded["account_id"])
    _seed_completed_backtest(client, seeded)
    _seed_paper_attest(client, seeded, days=14)
    r = client.post(f"{API}/wizard/check-admission", json={
        "strategy_id": seeded["strategy_id"], "version": seeded["version"],
        "account_id": seeded["account_id"]}, headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is True, body
    assert all(g["passed"] for g in body["gates"])


def test_admission_unknown_version_404(client, token, seeded):
    r = client.post(f"{API}/wizard/check-admission", json={
        "strategy_id": seeded["strategy_id"], "version": 99,
        "account_id": seeded["account_id"]}, headers=_auth(token))
    assert r.status_code == 404


# ── deploy ─────────────────────────────────────────────────────

def test_deploy_requires_all_gates(client, token, seeded):
    seeded["uid"] = _uid_of(client, token)
    # 权限闸放行（含实盘/模拟互斥类型校验），让回测/时长闸失败 → 仍验证
    # 「闸门未全过 → 409 结构化 detail」
    _mark_live_allowed(client, seeded["account_id"])
    resp = client.post(API, json=_create_body(seeded), headers=_auth(token))
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "准入闸门" in detail["message"]
    assert len(detail["failed_gates"]) >= 1


def test_deploy_wrong_account_tail_422(client, token, seeded):
    seeded["uid"] = _uid_of(client, token)
    _mark_live_allowed(client, seeded["account_id"])
    _seed_completed_backtest(client, seeded)
    _seed_paper_attest(client, seeded, days=14)
    resp = client.post(API, json=_create_body(seeded, account_tail="0000"),
                       headers=_auth(token))
    assert resp.status_code == 422
    assert "尾号" in resp.json()["detail"]


def test_deploy_missing_risk_rule_422(client, token, seeded):
    seeded["uid"] = _uid_of(client, token)
    _mark_live_allowed(client, seeded["account_id"])
    _seed_completed_backtest(client, seeded)
    _seed_paper_attest(client, seeded, days=14)
    resp = client.post(API, json=_create_body(
        seeded, rules=[{"type": "max_open_positions", "threshold": 5}]),
        headers=_auth(token))
    assert resp.status_code == 422
    assert "亏损/回撤" in str(resp.json()["detail"])


def test_deploy_no_disclaimer_422(client, token, seeded):
    seeded["uid"] = _uid_of(client, token)
    _mark_live_allowed(client, seeded["account_id"])
    _seed_completed_backtest(client, seeded)
    _seed_paper_attest(client, seeded, days=14)
    resp = client.post(API, json=_create_body(seeded, disclaimer_ack=False),
                       headers=_auth(token))
    assert resp.status_code == 422


def test_deploy_success_freezes_and_audits(client, token, seeded):
    seeded["uid"] = _uid_of(client, token)
    _mark_live_allowed(client, seeded["account_id"])
    _seed_completed_backtest(client, seeded)
    _seed_paper_attest(client, seeded, days=14)
    resp = client.post(API, json=_create_body(seeded), headers=_auth(token))
    assert resp.status_code == 201, resp.text
    run = resp.json()
    assert run["status"] in ("pending", "running")
    assert run["execution_mode"] == "standard"
    assert run["magic"] > 0
    assert run["observation_ends_at"] is not None
    detail = client.get(f"{API}/{run['id']}", headers=_auth(token)).json()
    snap = detail["admission_snapshot"]
    assert snap["allowed"] is True
    assert len(snap["gates"]) == 4
    assert detail["config_snapshot"]["account"]["login"] == seeded["login"]
    # audit trail: admission_pass + live_deploy both recorded, chain intact
    assert _audit_has(client, "live_deploy", run["id"])
    assert _audit_has(client, "admission_pass", seeded["strategy_id"])


def test_deploy_cross_user_404(client, token, seeded):
    seeded["uid"] = _uid_of(client, token)
    other = client.post(
        "/api/v1/auth/register",
        json={"email": f"lv2-{uuid.uuid4().hex[:8]}@test.com",
              "username": f"lv2{uuid.uuid4().hex[:8]}",
              "password": "Passw0rd!", "full_name": "越权"},
    ).json()["access_token"]
    resp = client.post(API, json=_create_body(seeded), headers=_auth(other))
    assert resp.status_code == 404


# ── list / detail ownership ────────────────────────────────────

def test_list_requires_auth(client):
    assert client.get(API).status_code == 401


# ── helpers ────────────────────────────────────────────────────

def _uid_of(client, token) -> str:
    me = client.get("/api/v1/auth/me", headers=_auth(token))
    if me.status_code == 200:
        return me.json()["id"]
    import jwt

    from app.config import settings

    payload = jwt.decode(token, settings.JWT_SECRET_KEY,
                         algorithms=[settings.JWT_ALGORITHM])
    return payload["sub"]


def _audit_has(client, action: str, target_id: str) -> bool:
    async def _q():
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog

        async with AsyncSessionLocal() as db:
            n = (await db.execute(
                select(AuditLog).where(
                    AuditLog.action == action,
                    AuditLog.target_id == target_id)
            )).scalars().first()
            return n is not None
    return client.portal.call(_q)
