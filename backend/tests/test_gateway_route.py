"""Per-account gateway routing tests (M12 Phase A).

Verifies resolve_gateway maps an account to its bound node's host:port (and
falls back to the global default), account_credentials decrypts the stored
password, and reconcile_once builds one client PER account node (the
single-terminal invariant: different accounts → different URLs).
"""

from __future__ import annotations

import uuid

import pytest


def _token(client) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"rt-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"rt{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "路由测试"},
    )
    assert r.status_code == 201
    return r.json()["access_token"]


@pytest.fixture
def token(client) -> str:
    return _token(client)


def _uid(client, token) -> str:
    from jose import jwt

    from app.config import settings

    return jwt.decode(token, settings.JWT_SECRET_KEY,
                      algorithms=[settings.JWT_ALGORITHM])["sub"]


def _seed_account(client, token, *, host: str, port: int,
                  password: str = "Secret123!") -> str:
    """Create node + MT5Account with encrypted password on the app loop."""
    from app.services import credentials

    uid_str = _uid(client, token)

    async def _ins():
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import GatewayNode, MT5Account

        uid = uuid.UUID(uid_str)
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
                login=900000 + (uuid.uuid4().int % 90000),
                server="MetaQuotes-Demo", name="rt", account_type="demo",
                leverage=100, currency="USD", gateway_node_id=node.id,
                user_id=uid, live_allowed=True,
                password_encrypted=credentials.encrypt_secret(password),
            )
            db.add(acct)
            await db.commit()
            return str(acct.id)

    return client.portal.call(_ins)


def test_resolve_gateway_returns_node_url(client, token):
    from app.services.gateway_route import resolve_gateway

    acct_id = _seed_account(client, token, host="10.0.0.5", port=8091)

    async def _run():
        from app.engine.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            return await resolve_gateway(db, uuid.UUID(acct_id))

    assert client.portal.call(_run) == "http://10.0.0.5:8091"


def test_resolve_gateway_falls_back_to_default(client, token):
    from app.config import settings
    from app.services.gateway_route import resolve_gateway

    async def _run():
        from app.engine.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            return await resolve_gateway(db, None)

    assert client.portal.call(_run) == settings.GATEWAY_URL


def test_account_credentials_decrypts_password(client, token):
    from app.services.gateway_route import account_credentials

    acct_id = _seed_account(client, token, host="10.0.0.6", port=8092,
                            password="MyPass!2026")

    async def _run():
        from app.engine.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            return await account_credentials(db, uuid.UUID(acct_id))

    login, password, server = client.portal.call(_run)
    assert password == "MyPass!2026"
    assert server == "MetaQuotes-Demo"
    assert login > 0


def test_reconcile_builds_client_per_account_node(client, token, monkeypatch):
    """Two accounts on two nodes → reconcile uses each node's URL, not global."""
    import app.services.live.reconcile as rec_mod

    acct_a = _seed_account(client, token, host="10.0.0.7", port=8093)
    acct_b = _seed_account(client, token, host="10.0.0.8", port=8094)

    seen_urls: list[str] = []

    class RecordingStub:
        def __init__(self, base_url=None):
            seen_urls.append(base_url or "")

        async def positions(self, symbol=None, timeout=None):
            return []

        async def account(self, timeout=None):
            return None

        async def aclose(self):
            pass

    monkeypatch.setattr(rec_mod, "GatewayClient", RecordingStub)

    # seed a running live run for each account so reconcile picks them up
    uid_str = _uid(client, token)

    def _seed_run(account_id: str) -> None:
        async def _ins():
            from decimal import Decimal

            from app.engine.session import AsyncSessionLocal
            from app.models import LiveRun, Strategy

            uid = uuid.UUID(uid_str)
            async with AsyncSessionLocal() as db:
                strat = Strategy(user_id=uid, name=f"rr-{uuid.uuid4().hex[:8]}",
                                 timeframe="H1", status="active")
                db.add(strat)
                await db.flush()
                db.add(LiveRun(
                    user_id=uid, strategy_id=strat.id,
                    account_id=uuid.UUID(account_id), source_code="x",
                    symbol="EURUSD", timeframe="H1", execution_mode="standard",
                    allocated_capital=Decimal("1000"), leverage=100,
                    magic=90000001, status="running",
                    balance=Decimal("1000"), equity=Decimal("1000"),
                    margin_free=Decimal("1000"), unrealized_pnl=Decimal("0"),
                    peak_equity=Decimal("1000"), day_start_equity=Decimal("1000"),
                    day_start_date="2026-10-11",
                ))
                await db.commit()
        client.portal.call(_ins)

    _seed_run(acct_a)
    _seed_run(acct_b)

    client.portal.call(rec_mod.reconcile_once)

    assert "http://10.0.0.7:8093" in seen_urls
    assert "http://10.0.0.8:8094" in seen_urls


# ── paper manager routing (M12 Phase B) ────────────────────────

_SYMBOL_INFO = {
    "name": "EURUSD", "description": "Euro", "digits": 5, "point": 0.00001,
    "spread": 20, "volume_step": 0.01, "volume_min": 0.01, "volume_max": 100.0,
    "trade_contract_size": 100000.0, "margin_initial": 0.0,
    "currency_base": "EUR", "currency_profit": "USD", "trade_mode": 4,
}


def _seed_paper_run(client, token, account_id: str | None) -> str:
    uid_str = _uid(client, token)

    async def _ins() -> str:
        from decimal import Decimal

        from app.engine.session import AsyncSessionLocal
        from app.models import PaperRun, Strategy

        uid = uuid.UUID(uid_str)
        async with AsyncSessionLocal() as db:
            strat = Strategy(user_id=uid, name=f"pr-{uuid.uuid4().hex[:8]}",
                             timeframe="H1", status="active")
            db.add(strat)
            await db.flush()
            run = PaperRun(
                user_id=uid, strategy_id=strat.id,
                account_id=uuid.UUID(account_id) if account_id else None,
                source_code="def on_init(ctx):\n    pass\n",
                symbol="EURUSD", timeframe="H1", execution_mode="standard",
                initial_deposit=Decimal("1000"), leverage=100,
                spread_mode="current", commission_per_lot=Decimal("0"),
                warmup_bars=10, parameters={}, config_snapshot={"seed": 1},
                balance=Decimal("1000"), equity=Decimal("1000"),
                margin_free=Decimal("1000"), status="pending",
            )
            db.add(run)
            await db.commit()
            return str(run.id)

    return client.portal.call(_ins)


def _paper_status_and_error(client, run_id: str) -> tuple[str, str]:
    async def _q():
        from app.engine.session import AsyncSessionLocal
        from app.models import PaperRun

        async with AsyncSessionLocal() as db:
            run = await db.get(PaperRun, uuid.UUID(run_id))
            return run.status, run.error_message or ""

    return client.portal.call(_q)


def _paper_stub(monkeypatch, *, login_error: bool = False):
    import app.services.paper.manager as pm_mod

    seen: dict = {"urls": [], "logins": []}

    class Stub:
        def __init__(self, base_url=None):
            seen["urls"].append(base_url or "")

        async def login(self, login, password, server):
            seen["logins"].append((login, password, server))
            if login_error:
                from app.services.gateway_client import GatewayLoginError

                raise GatewayLoginError("终端拒绝该账户登录")
            return {}

        async def symbols(self):
            return [dict(_SYMBOL_INFO)]

        async def rates(self, name, timeframe, limit):
            return []  # stop before spawn: history-empty fail path

        async def aclose(self):
            pass

    monkeypatch.setattr(pm_mod, "GatewayClient", Stub)
    return seen


def test_paper_start_routes_and_logins_bound_account(client, token, monkeypatch):
    from app.services.paper.manager import PaperManager

    acct_id = _seed_account(client, token, host="10.0.0.9", port=8095,
                            password="PaperPw!77")
    seen = _paper_stub(monkeypatch)
    run_id = _seed_paper_run(client, token, acct_id)

    client.portal.call(PaperManager().start, run_id)

    assert "http://10.0.0.9:8095" in seen["urls"]
    assert len(seen["logins"]) == 1
    login, password, server = seen["logins"][0]
    assert password == "PaperPw!77"
    assert server == "MetaQuotes-Demo"
    assert login > 0
    status, msg = _paper_status_and_error(client, run_id)
    assert status == "failed"
    assert "历史数据" in msg  # reached the history-empty gate, no spawn


def test_paper_start_legacy_row_skips_login(client, token, monkeypatch):
    from app.config import settings
    from app.services.paper.manager import PaperManager

    seen = _paper_stub(monkeypatch)
    run_id = _seed_paper_run(client, token, None)

    client.portal.call(PaperManager().start, run_id)

    assert seen["urls"] == [settings.GATEWAY_URL]
    assert seen["logins"] == []


def test_paper_start_login_failure_marks_run_failed(client, token, monkeypatch):
    from app.services.paper.manager import PaperManager

    acct_id = _seed_account(client, token, host="10.0.0.10", port=8096,
                            password="BadPw!1")
    _paper_stub(monkeypatch, login_error=True)
    run_id = _seed_paper_run(client, token, acct_id)

    client.portal.call(PaperManager().start, run_id)

    status, msg = _paper_status_and_error(client, run_id)
    assert status == "failed"
    assert "网关登录绑定账户失败" in msg
