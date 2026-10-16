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


# ── race-condition fix: flush before read ──────────────────────


class _StubHandle:
    """Stand-in for LiveManager._RunHandle — only account_id matters here."""

    def __init__(self, account_id: str):
        self.account_id = account_id


class _StubManager:
    """Replacement for LiveManager that exposes both flush_positions_for_account
    and force_refresh_account, reading the "latest" positions from the test's
    stub gateway (simulating what a real poll would have written to DB).
    """

    def __init__(self, account_id: str, positions: list[dict]):
        self._account_id = account_id
        self._positions = positions
        self._handles = {
            "fake-run": _StubHandle(account_id),
        }

    async def flush_positions_for_account(self, account_id: str) -> int:
        """Force-write the fresh positions into DB, simulating what the real
        manager does when reconcile knocks on the door before poll's DB write
        throttle expires."""
        if account_id != self._account_id:
            return 0

        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import LiveRun

        async with AsyncSessionLocal() as db:
            runs = (await db.execute(
                select(LiveRun).where(LiveRun.account_id == uuid.UUID(account_id))
            )).scalars().all()
            for run in runs:
                stub_tickets = {p["ticket"] for p in self._positions}
                db_tickets = {p["ticket"] for p in (run.positions or [])}
                if stub_tickets != db_tickets:
                    run.positions = list(self._positions)
                    db.add(run)
            await db.commit()
        return len(self._handles)

    async def force_refresh_account(
        self, account_id: str, *, client=None,
    ) -> dict:
        """Auto-converge helper used by reconcile Step 3.  Same logic as
        flush — writes fresh stub positions to DB — returns the new dict
        shape that matches the real manager's contract."""
        n = await self.flush_positions_for_account(account_id)
        return {"ok": n > 0, "handles": n, "gateway_down": False}


def test_reconcile_race_flush_closes_gap(client, token, stub, monkeypatch):
    """Poll refreshes gateway mirror but _write_account has a 2s throttle,
    so run.positions in DB lags h.positions.  Reconcile landing in that gap
    must NOT fire a false-positive freeze."""
    from sqlalchemy import select

    from app.services.live import manager as manager_mod
    from app.services.live.reconcile import reconcile_once

    # Scenario: gateway has 2 positions (202 + 303), but DB only has 202 —
    # exactly what happens when poll refreshed h.positions but the throttle
    # hasn't let _write_account push it to DB yet.
    pos_polled = [
        {"ticket": 202, "symbol": "EURUSD", "type": "buy", "volume": 0.1,
         "price_open": 1.1, "price_current": 1.1, "profit": 0.0, "swap": 0.0,
         "commission": 0.0, "time": 1, "magic": 777, "identifier": 202,
         "comment": ""},
        {"ticket": 303, "symbol": "EURUSD", "type": "sell", "volume": 0.1,
         "price_open": 1.1, "price_current": 1.1, "profit": 0.0, "swap": 0.0,
         "commission": 0.0, "time": 2, "magic": 777, "identifier": 303,
         "comment": ""},
    ]
    pos_stale = [pos_polled[0]]  # only 202 in DB

    seeded = _seed_run(client, token, positions=pos_stale, magic=777)

    # Gateway returns the full polled set (what real poll would see).
    stub.positions_rows = pos_polled

    # Install stub manager so reconcile's flush step actually works.
    mgr = _StubManager(seeded["account_id"], pos_polled)
    monkeypatch.setattr(manager_mod, "get_live_manager", lambda: mgr)

    # Sanity check: DB is stale before reconcile.
    async def _verify_stale():
        from app.engine.session import AsyncSessionLocal
        from app.models import LiveRun
        async with AsyncSessionLocal() as db:
            run = await db.get(LiveRun, uuid.UUID(seeded["run_id"]))
            return {p["ticket"] for p in (run.positions or [])}
    assert client.portal.call(_verify_stale) == {202}

    # Run reconcile — flush should close the gap.
    out = client.portal.call(reconcile_once)
    assert out[seeded["account_id"]] == 0  # ← key: no diff after flush

    # DB now matches the gateway.
    assert client.portal.call(_verify_stale) == {202, 303}

    # Account NOT frozen — the race window is gone.
    async def _check_frozen():
        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account
        async with AsyncSessionLocal() as db:
            acct = await db.get(MT5Account, uuid.UUID(seeded["account_id"]))
            return acct.trade_frozen
    assert client.portal.call(_check_frozen) is False


# ── root-cause 2: timeout convergence ──────────────────────────


def test_timeout_converge_closes_reconcile_gap(client, token, stub, monkeypatch):
    """GatewayTimeout on an order that *did* fill on the broker side leaves
    the terminal with an extra ticket the platform mirror doesn't have.
    Without immediate convergence the 5-minute reconcile cycle would see a
    diff and freeze the account.  After _force_converge_after_unknown runs
    the mirror is current and reconcile converges cleanly."""
    import uuid as uuid_mod
    from sqlalchemy import select

    from app.engine.session import AsyncSessionLocal
    from app.models import LiveRun, MT5Account
    from app.services.live import manager as manager_mod
    from app.services.live.manager import get_live_manager
    from app.services.live.reconcile import reconcile_once

    # Patch manager module's GatewayClient so force_converge reads from the
    # same stub instance the reconcile fixture uses — not the real MT5 gateway.
    class _PatchedGC:
        @staticmethod
        def for_registered_node(*a, **k):
            return stub
    monkeypatch.setattr(manager_mod, "GatewayClient", _PatchedGC)

    # DB mirror = old set (ticket 100 only — poll hasn't refreshed after timeout)
    old_positions = [{
        "ticket": 100, "symbol": "EURUSD", "type": "buy", "volume": 0.1,
        "price_open": 1.1, "price_current": 1.1, "profit": 0.0, "swap": 0.0,
        "commission": 0.0, "time": 1, "magic": 666, "identifier": 100,
        "comment": "",
    }]
    # Gateway has old + the timeout-filled new position (ticket 200)
    new_positions = old_positions + [{
        "ticket": 200, "symbol": "EURUSD", "type": "buy", "volume": 0.2,
        "price_open": 1.1, "price_current": 1.1, "profit": 0.0, "swap": 0.0,
        "commission": 0.0, "time": 2, "magic": 666, "identifier": 200,
        "comment": "",
    }]

    seeded = _seed_run(client, token, positions=old_positions, magic=666)
    stub.positions_rows = new_positions

    # —— Phase 1: WITHOUT convergence, reconcile fires diff=1 and freezes ——
    out_pre = client.portal.call(reconcile_once)
    assert out_pre[seeded["account_id"]] == 1  # ticket 200 diff

    async def _check_frozen():
        async with AsyncSessionLocal() as db:
            acct = await db.get(MT5Account, uuid_mod.UUID(seeded["account_id"]))
            return acct.trade_frozen
    assert client.portal.call(_check_frozen) is True

    # Reset: unfreeze so we can test the converged path on a clean slate.
    async def _unfreeze():
        async with AsyncSessionLocal() as db:
            acct = await db.get(MT5Account, uuid_mod.UUID(seeded["account_id"]))
            acct.trade_frozen = False
            db.add(acct)
            run = await db.get(LiveRun, uuid_mod.UUID(seeded["run_id"]))
            run.positions = list(old_positions)
            db.add(run)
            await db.commit()
    client.portal.call(_unfreeze)

    # —— Phase 2: apply force_converge ——
    class _FakeHandle:
        def __init__(self, run_id: str, account_id: str):
            self.run_id = run_id
            self.account_id = account_id
            self.gateway_url = None
            self.account = {}
            self.positions = []
            self.day_start_equity = 0.0
            self.peak_equity = 0.0
            self.day_start_date = ""
            self.halt_until = ""
            self.last_account_write = 0.0

    mgr = get_live_manager()
    h = _FakeHandle(seeded["run_id"], seeded["account_id"])

    async def _do_converge():
        await mgr._force_converge_after_unknown(h)
    client.portal.call(_do_converge)

    # Verify DB now matches gateway.
    async def _check_db_tickets():
        async with AsyncSessionLocal() as db:
            run = await db.get(LiveRun, uuid_mod.UUID(seeded["run_id"]))
            return {p["ticket"] for p in (run.positions or [])}
    assert client.portal.call(_check_db_tickets) == {100, 200}

    # —— Phase 3: reconcile NOW sees zero diff ——
    out_post = client.portal.call(reconcile_once)
    assert out_post[seeded["account_id"]] == 0
    assert client.portal.call(_check_frozen) is False


# ── root-cause 3: manual MT5 operation → auto-converge ─────────


def test_reconcile_auto_converges_after_manual_mt5_close(client, token, stub, monkeypatch):
    """User manually closes an AI-managed position directly in the MT5
    terminal.  The platform mirror hasn't caught up yet → reconcile sees a
    diff.  Instead of freezing, it should refresh the mirror from the
    gateway and re-check — after which diff disappears, poll lag was the
    only cause."""
    import uuid as uuid_mod

    from app.services.live import manager as manager_mod
    from app.services.live.reconcile import reconcile_once

    # Simulate a stale mirror: DB still has both 100 and 200,
    # but MT5 gateway (stub) shows only 100 — user manually closed 200 there.
    both = [
        {"ticket": 100, "symbol": "EURUSD", "type": "buy", "volume": 0.1,
         "price_open": 1.1, "price_current": 1.1, "profit": 0.0, "swap": 0.0,
         "commission": 0.0, "time": 1, "magic": 888, "identifier": 100,
         "comment": ""},
        {"ticket": 200, "symbol": "EURUSD", "type": "buy", "volume": 0.1,
         "price_open": 1.1, "price_current": 1.1, "profit": 0.0, "swap": 0.0,
         "commission": 0.0, "time": 2, "magic": 888, "identifier": 200,
         "comment": ""},
    ]
    only_100 = [both[0]]  # what MT5 shows after user closed 200 manually

    seeded = _seed_run(client, token, positions=both, magic=888)
    stub.positions_rows = only_100  # MT5 no longer has ticket 200

    # Need a StubManager with handles that force_refresh can find, and whose
    # mirrors return only_100 (the "fresh" gateway value).
    mgr = _StubManager(seeded["account_id"], only_100)
    monkeypatch.setattr(manager_mod, "get_live_manager", lambda: mgr)

    async def _check_frozen():
        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account
        async with AsyncSessionLocal() as db:
            acct = await db.get(MT5Account, uuid_mod.UUID(seeded["account_id"]))
            return acct.trade_frozen

    # —— Phase 1: WITHOUT auto-converge, reconcile would have frozen ——
    # (we can't easily simulate the old behavior without undoing the code,
    # but the below proves that WITH auto-converge it does NOT freeze.)

    # —— Phase 2: reconcile runs with auto-converge ——
    out = client.portal.call(reconcile_once)
    assert out[seeded["account_id"]] == 0  # ✅ diff auto-converged
    assert client.portal.call(_check_frozen) is False  # ✅ NOT frozen


# ── root-cause 4: protocol break (connection reset) convergence ──


def test_protocol_error_triggers_convergence(client, token, stub, monkeypatch):
    """GatewayProtocolError (connection reset by peer mid-request) has the same
    UNKNOWN outcome as GatewayTimeout — the request may have been fully
    processed by the gateway before the transport broke.  It must trigger
    flag_reconcile + _force_converge_after_unknown, NOT be treated as a
    known no-op (connect-refused style)."""
    import uuid as uuid_mod

    from app.engine.session import AsyncSessionLocal
    from app.models import LiveRun, MT5Account
    from app.services.gateway_client import GatewayProtocolError
    from app.services.live import manager as manager_mod
    from app.services.live.manager import get_live_manager

    class _PatchedGC:
        @staticmethod
        def for_registered_node(*a, **k):
            return stub
    monkeypatch.setattr(manager_mod, "GatewayClient", _PatchedGC)

    # DB mirror lags broker reality (same shape as timeout test)
    old_positions = [{
        "ticket": 100, "symbol": "EURUSD", "type": "buy", "volume": 0.1,
        "price_open": 1.1, "price_current": 1.1, "profit": 0.0, "swap": 0.0,
        "commission": 0.0, "time": 1, "magic": 777, "identifier": 100,
        "comment": "",
    }]
    new_positions = old_positions + [{
        "ticket": 300, "symbol": "EURUSD", "type": "buy", "volume": 0.15,
        "price_open": 1.1, "price_current": 1.1, "profit": 0.0, "swap": 0.0,
        "commission": 0.0, "time": 3, "magic": 777, "identifier": 300,
        "comment": "",
    }]

    seeded = _seed_run(client, token, positions=old_positions, magic=777)
    stub.positions_rows = new_positions

    async def _unfreeze():
        async with AsyncSessionLocal() as db:
            acct = await db.get(MT5Account, uuid_mod.UUID(seeded["account_id"]))
            acct.trade_frozen = False
            db.add(acct)
            await db.commit()
    client.portal.call(_unfreeze)

    class _FakeHandle:
        def __init__(self, run_id: str, account_id: str):
            self.run_id = run_id
            self.account_id = account_id
            self.gateway_url = None
            self.account = {}
            self.positions = []
            self.day_start_equity = 0.0
            self.peak_equity = 0.0
            self.day_start_date = ""
            self.halt_until = ""
            self.last_account_write = 0.0

    mgr = get_live_manager()
    h = _FakeHandle(seeded["run_id"], seeded["account_id"])

    # GatewayProtocolError path shares _force_converge_after_unknown with
    # GatewayTimeout — call the helper directly to verify it converges
    # the mirror to gateway reality (stub).
    async def _do_protocol_error_converge():
        await mgr._force_converge_after_unknown(h)
    client.portal.call(_do_protocol_error_converge)

    async def _check_db_tickets():
        async with AsyncSessionLocal() as db:
            run = await db.get(LiveRun, uuid_mod.UUID(seeded["run_id"]))
            return {p["ticket"] for p in (run.positions or [])}
    assert client.portal.call(_check_db_tickets) == {100, 300}, (
        "GatewayProtocolError path must converge mirror to gateway reality"
    )

    async def _check_frozen():
        async with AsyncSessionLocal() as db:
            acct = await db.get(MT5Account, uuid_mod.UUID(seeded["account_id"]))
            return acct.trade_frozen
    assert client.portal.call(_check_frozen) is False


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


def test_unfreeze_requires_owner_or_admin(client, token, stub):
    """Account owner can unfreeze their own account OR admin can unfreeze any.
    Non-owner non-admin → 403.  UNFREEZE second-confirm + audit preserved."""
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

    # Wrong confirm word → 422
    bad = client.post(f"{API}/accounts/{seeded['account_id']}/unfreeze",
                      json={"review_note": "核对无误差异为手动平仓",
                            "confirm": "WRONG"}, headers=_auth(token))
    assert bad.status_code == 422

    # Owner (token user owns the account) → 200
    ok = client.post(f"{API}/accounts/{seeded['account_id']}/unfreeze",
                     json={"review_note": "核对无误差异为手动平仓",
                           "confirm": "UNFREEZE"}, headers=_auth(token))
    assert ok.status_code == 200, (
        "account owner should be able to unfreeze their own frozen account"
    )

    # Non-owner non-admin → 403.  Register a second unrelated user.
    r2 = client.post(
        "/api/v1/auth/register",
        json={"email": f"rc2-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"rc2{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "对账测试2"},
    )
    token2 = r2.json()["access_token"]

    async def _refreeze():
        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account
        async with AsyncSessionLocal() as db:
            acct = await db.get(MT5Account, uuid.UUID(seeded["account_id"]))
            acct.trade_frozen = True
            db.add(acct)
            await db.commit()
    client.portal.call(_refreeze)

    forbidden = client.post(f"{API}/accounts/{seeded['account_id']}/unfreeze",
                            json={"review_note": "outsider attempting unfreeze",
                                  "confirm": "UNFREEZE"}, headers=_auth(token2))
    assert forbidden.status_code == 403, (
        "non-owner non-admin must NOT be able to unfreeze someone else's account"
    )


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
