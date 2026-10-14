"""EA bridge API tests (P4 方案 B, EB-1).

Pair flow (one-shot code → ea_bridge node + node_key), X-EA-Key auth,
tick ingestion with cursor dedup, buffer overflow drop-oldest, command
queue + req_id futures, feature-flag 404, and the EA-node run guards.
MQL5-side skew normalization cannot run under pytest — it is covered by
code review + the real-terminal smoke (EB-4), not here.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import CheckConstraint

from app.config import settings
from app.services import pair_store
from app.services.ea_bridge import registry

API = "/api/v1/ea"
GW_API = "/api/v1/gateway"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, *, role: str = "viewer") -> dict:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"ea-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"ea{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "EA桥测试"},
    )
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    if role == "admin":
        from jose import jwt
        from sqlalchemy import update

        from app.engine.session import AsyncSessionLocal
        from app.models import User

        uid = jwt.decode(token, settings.JWT_SECRET_KEY,
                         algorithms=[settings.JWT_ALGORITHM])["sub"]

        async def _up():
            async with AsyncSessionLocal() as db:
                await db.execute(update(User).where(User.id == uuid.UUID(uid))
                                 .values(role="admin"))
                await db.commit()
        client.portal.call(_up)
    return {"Authorization": f"Bearer {token}"}


def _mint_code(client: TestClient, admin: dict) -> str:
    pair_store.reset()
    pair_store.reset_attempts()
    r = client.post(f"{GW_API}/pair-codes", headers=admin)
    assert r.status_code == 201, r.text
    return r.json()["code"]


def _pair(client: TestClient, code: str, *, login: int = 9123004567,
          server: str = "MetaQuotes-Demo") -> dict:
    r = client.post(f"{API}/pair", json={
        "pair_code": code, "mt5_login": login, "mt5_server": server,
        "terminal_info": {"mt5_version": "5.42.0"},
    })
    assert r.status_code == 200, r.text
    return r.json()


def _audit_count(client: TestClient, action: str) -> int:
    async def _q():
        from sqlalchemy import func as sfunc
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog

        async with AsyncSessionLocal() as db:
            return (await db.execute(
                select(sfunc.count()).select_from(AuditLog)
                .where(AuditLog.action == action)
            )).scalar_one()
    return client.portal.call(_q)


# ── pair flow ─────────────────────────────────────────────────

def test_pair_flow_creates_ea_bridge_node(client):
    registry.reset()
    admin = _register(client, role="admin")
    code = _mint_code(client, admin)
    before = _audit_count(client, "ea.node_pair")

    paired = _pair(client, code)
    assert len(paired["node_key"]) == 64  # 32B hex
    assert paired["sync_interval_ms"] == 500

    nodes = client.get(f"{GW_API}/nodes", headers=admin).json()["items"]
    node = next(n for n in nodes if n["id"] == paired["node_id"])
    assert node["type"] == "ea_bridge"
    assert node["host"] == "ea:MetaQuotes-Demo:9123004567"
    assert node["port"] == 0
    assert node["metadata"].get("mt5_version") == "5.42.0"
    # node_key 是节点凭据：绝不进节点列表响应
    assert "node_key" not in node["metadata"].values()

    assert _audit_count(client, "ea.node_pair") == before + 1
    registry.reset()


def test_pair_rejects_bad_code(client):
    pair_store.reset()
    pair_store.reset_attempts()
    r = client.post(f"{API}/pair", json={
        "pair_code": "ZZZZ-ZZZZ", "mt5_login": 123,
        "mt5_server": "Demo", "terminal_info": {},
    })
    assert r.status_code == 409


def test_endpoints_404_when_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "EA_BRIDGE_ENABLED", False)
    assert client.post(f"{API}/pair", json={
        "pair_code": "AAAA-AAAA", "mt5_login": 1, "mt5_server": "s",
    }).status_code == 404
    assert client.post(f"{API}/sync", json={"ticks": {}}).status_code == 404
    assert client.post(f"{API}/result", json={
        "req_id": "abcd1234", "ok": True,
    }).status_code == 404


# ── X-EA-Key auth + sync ingestion ────────────────────────────

def _paired_headers(client: TestClient) -> tuple[dict, str]:
    admin = _register(client, role="admin")
    code = _mint_code(client, admin)
    paired = _pair(client, code)
    return {"X-EA-Key": paired["node_key"]}, paired["node_id"]


def test_sync_requires_key_401(client):
    assert client.post(f"{API}/sync", json={"ticks": {}}).status_code == 401
    assert client.post(f"{API}/sync", json={"ticks": {}},
                       headers={"X-EA-Key": "deadbeef" * 8}).status_code == 401


def test_sync_ingests_ticks_and_marks_node_online(client):
    registry.reset()
    headers, node_id = _paired_headers(client)

    r = client.post(f"{API}/sync", json={
        "cursor_last_msc": 100,
        "ticks": {"eurusd": [[1500, 1.1, 1.1002], [1700, 1.2, 1.2002]]},
    }, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["commands"] == [] and body["server_time_ms"] > 0

    # 大小写归一 + 游标过滤由调用方做 —— 注册表原样缓冲
    assert registry.take_ticks(node_id, "EURUSD") == [
        [1500, 1.1, 1.1002], [1700, 1.2, 1.2002]]
    assert registry.take_ticks(node_id, "EURUSD") == []  # 破坏性读取

    async def _q():
        from app.engine.session import AsyncSessionLocal
        from app.models import GatewayNode

        async with AsyncSessionLocal() as db:
            node = await db.get(GatewayNode, uuid.UUID(node_id))
            return node.status, node.last_heartbeat is not None
    assert client.portal.call(_q) == ("online", True)
    registry.reset()


def test_sync_caps_rows_per_call(client, monkeypatch):
    registry.reset()
    monkeypatch.setattr(settings, "EA_SYNC_MAX_TICKS", 3)
    headers, node_id = _paired_headers(client)
    rows = [[i, 1.0, 1.0] for i in range(10)]

    r = client.post(f"{API}/sync", json={"ticks": {"EURUSD": rows}},
                    headers=headers)
    assert r.status_code == 200
    taken = registry.take_ticks(node_id, "EURUSD")
    assert len(taken) == 3  # 截断到上限
    registry.reset()


def test_registry_buffer_overflow_drops_oldest(client, monkeypatch):
    registry.reset()
    monkeypatch.setattr(settings, "EA_TICK_BUFFER_MAX", 5)
    rows = [[i, 1.0, 1.0] for i in range(8)]
    assert registry.push_ticks("n1", "EURUSD", rows, max_rows=100) == 8
    assert registry.take_ticks("n1", "EURUSD") == [[i, 1.0, 1.0] for i in range(3, 8)]
    assert registry.dropped_count("n1", "EURUSD") == 3  # 丢最旧 + 计数
    registry.reset()


def test_repair_rotates_key(client):
    registry.reset()
    admin = _register(client, role="admin")

    first = _pair(client, _mint_code(client, admin), login=9123004999)
    old_headers = {"X-EA-Key": first["node_key"]}
    assert client.post(f"{API}/sync", json={"ticks": {}},
                       headers=old_headers).status_code == 200

    # 同一终端（同 host）再次配对 → 旧 key 立即失效
    second = _pair(client, _mint_code(client, admin), login=9123004999)
    assert second["node_id"] == first["node_id"]
    assert client.post(f"{API}/sync", json={"ticks": {}},
                       headers=old_headers).status_code == 401
    assert client.post(f"{API}/sync", json={"ticks": {}},
                       headers={"X-EA-Key": second["node_key"]}).status_code == 200
    registry.reset()


# ── command queue + req_id futures ────────────────────────────

def test_command_roundtrip_via_result(client):
    registry.reset()
    headers, node_id = _paired_headers(client)
    req_id = registry.enqueue_command(node_id, "rates_req", {"bars": 100})

    async def _mk_fut():
        return registry.new_future(node_id, req_id)
    fut = client.portal.call(_mk_fut)

    body = client.post(f"{API}/sync", json={"ticks": {}},
                       headers=headers).json()
    assert body["commands"] == [
        {"type": "rates_req", "req_id": req_id, "params": {"bars": 100}}]
    # drain 后不重复下发
    body2 = client.post(f"{API}/sync", json={"ticks": {}},
                        headers=headers).json()
    assert body2["commands"] == []

    r = client.post(f"{API}/result", json={
        "req_id": req_id, "ok": True, "payload": {"rows": [[1, 2, 3, 4, 5]]},
    }, headers=headers)
    assert r.status_code == 200 and r.json()["resolved"] is True
    assert fut.done() and fut.result()["ok"] is True
    assert fut.result()["payload"] == {"rows": [[1, 2, 3, 4, 5]]}
    registry.reset()


def test_result_stale_req_resolves_false(client):
    headers, _ = _paired_headers(client)
    r = client.post(f"{API}/result", json={
        "req_id": "no-such-req", "ok": False, "error": "late",
    }, headers=headers)
    assert r.status_code == 200 and r.json()["resolved"] is False


async def test_wait_result_times_out(monkeypatch):
    registry.reset()
    monkeypatch.setattr(settings, "EA_RESULT_TIMEOUT", 0.05)
    req_id = registry.enqueue_command("n9", "rates_req")
    registry.new_future("n9", req_id)  # 入队 + 建 future 是两步（manager 侧）
    out = await registry.wait_result("n9", req_id)
    assert out == {"ok": False, "payload": {}, "error": "RESULT_TIMEOUT"}
    registry.reset()


# ── EB-2：rates RPC + 模拟盘完整数据面 ────────────────────────

def _seed_ea_account(client: TestClient, token: str) -> tuple[str, str]:
    """Create an ea_bridge node + MT5Account bound to it → (acct_id, node_id)."""
    uid_str = _uid_of(client, token)

    async def _ins():
        from app.engine.session import AsyncSessionLocal
        from app.models import GatewayNode, MT5Account
        from app.services import credentials

        async with AsyncSessionLocal() as db:
            node = GatewayNode(
                name="EA·测试", type="ea_bridge",
                host=f"ea:MetaQuotes-Demo:{uid_str[:8]}", port=0,
                status="online",
                ea_key_hash=(uuid.uuid4().hex + uuid.uuid4().hex),
            )
            db.add(node)
            await db.flush()
            acct = MT5Account(
                login=900000 + (uuid.uuid4().int % 90000),
                server="MetaQuotes-Demo", name="ea", account_type="demo",
                leverage=100, currency="USD", gateway_node_id=node.id,
                user_id=uuid.UUID(uid_str), live_allowed=True,
                password_encrypted=credentials.encrypt_secret("Secret123!"),
            )
            db.add(acct)
            await db.commit()
            return str(acct.id), str(node.id)
    return client.portal.call(_ins)


def _uid_of(client: TestClient, token: str) -> str:
    from jose import jwt

    return jwt.decode(token, settings.JWT_SECRET_KEY,
                      algorithms=[settings.JWT_ALGORITHM])["sub"]


def _seed_run(client: TestClient, token: str, acct_id: str,
              *, status: str = "pending", warmup_bars: int = 5) -> str:
    uid_str = _uid_of(client, token)

    async def _ins():
        from decimal import Decimal

        from app.engine.session import AsyncSessionLocal
        from app.models import PaperRun, Strategy

        async with AsyncSessionLocal() as db:
            strat = Strategy(user_id=uuid.UUID(uid_str), name="ea-eb2",
                             timeframe="H1", status="active")
            db.add(strat)
            await db.flush()
            run = PaperRun(
                user_id=uuid.UUID(uid_str), strategy_id=strat.id,
                account_id=uuid.UUID(acct_id),
                source_code="def on_init(ctx):\n    pass\n",
                symbol="EURUSD", timeframe="H1", execution_mode="standard",
                initial_deposit=Decimal("1000"), leverage=100,
                spread_mode="current", commission_per_lot=Decimal("0"),
                warmup_bars=warmup_bars, parameters={}, config_snapshot={},
                balance=Decimal("1000"), equity=Decimal("1000"),
                margin_free=Decimal("1000"), status=status,
            )
            db.add(run)
            await db.commit()
            return str(run.id)
    return client.portal.call(_ins)


def _get_run(client: TestClient, run_id: str) -> dict:
    async def _q():
        from app.engine.session import AsyncSessionLocal
        from app.models import PaperRun

        async with AsyncSessionLocal() as db:
            run = await db.get(PaperRun, uuid.UUID(run_id))
            return {
                "status": run.status,
                "error_code": run.error_code,
                "error_message": run.error_message or "",
                "snapshot": dict(run.config_snapshot or {}),
                "balance": float(run.balance),
                "equity": float(run.equity),
                "margin_used": float(run.margin_used),
                "unrealized_pnl": float(run.unrealized_pnl),
                "positions": list(run.positions or []),
                "equity_history": list(run.equity_history or []),
                "last_bar_time": run.last_bar_time,
            }
    return client.portal.call(_q)


def _wait_run_status(client: TestClient, run_id: str, want: str,
                     timeout: float = 5.0) -> dict:
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        row = _get_run(client, run_id)
        if row["status"] == want:
            return row
        _time.sleep(0.05)
    return row


def _spawn_fake_ea(client: TestClient, node_id: str, bars: list, info: dict):
    """Fake EA：消费命令队列并把 rates_req 应答回注册表（同 loop 常驻）。"""

    async def _responder():
        while True:
            for cmd in registry.drain_commands(node_id):
                if cmd["type"] == "rates_req":
                    registry.resolve_result(
                        node_id, cmd["req_id"], True,
                        {"bars": bars, "symbol_info": info}, None,
                    )
            await asyncio.sleep(0.005)

    async def _go():
        return asyncio.create_task(_responder())
    return client.portal.call(_go)


_EA_INFO = {
    "description": "Euro vs US Dollar", "path": "Forex\\EURUSD",
    "currency_base": "EUR", "currency_profit": "USD",
    "digits": 5, "point": 0.00001, "volume_step": 0.01,
    "volume_min": 0.01, "volume_max": 100.0,
    "trade_contract_size": 100000.0, "margin_initial": 0.0,
    "trade_mode": 4, "spread": 1, "swap_long": 0.0, "swap_short": 0.0,
}


def _bars(n: int) -> list:
    """n 根已收盘 H1 bar（升序，远离 now —— 不受 _select_history 过滤）。"""
    import time as _time

    now = int(_time.time())
    t0 = now - (n + 2) * 3600
    return [[t0 + i * 3600, 1.10, 1.12, 1.09, 1.11, 100 + i, 1]
            for i in range(n)]


def test_paper_create_on_ea_node_allowed(client):
    """EB-2 解除 409 守卫：EA 节点账户可创建模拟盘（201），启动 fail-fast 落库。"""
    registry.reset()
    token = _register(client)["Authorization"].split(" ")[1]
    acct_id, _ = _seed_ea_account(client, token)
    sid = client.post("/api/v1/strategies", json={
        "name": f"ea-{uuid.uuid4().hex[:8]}", "language": "python",
        "direction": "both", "symbol_ids": ["EURUSD"], "timeframe": "H1",
        "tags": [], "source_code": "def on_init(ctx):\n    pass\n",
    }, headers=_auth(token)).json()["id"]

    r = client.post("/api/v1/paper", json={
        "strategy_id": sid, "account_id": acct_id, "symbol": "EURUSD",
        "timeframe": "H1", "execution_mode": "standard",
        "initial_deposit": 1000, "leverage": 100, "warmup_bars": 10,
        "spread_mode": "current", "assumption_ack": True,
    }, headers=_auth(token))
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]
    if settings.PAPER_ENABLED:
        # spawn_start 是 fire-and-forget：EA 未上线 → GATEWAY_OFFLINE fail-fast
        row = _wait_run_status(client, run_id, "failed")
        assert row["status"] == "failed"
        assert row["error_code"] == "GATEWAY_OFFLINE"
        assert "未上线" in row["error_message"]
    registry.reset()


async def test_request_ea_rates_roundtrip_and_clamp():
    """rates RPC 两种语义：count 最新 N 根 / start-end 区间；count 上限截断。"""
    from app.services.ea_bridge import EA_RATES_MAX_BARS, request_ea_rates

    registry.reset()
    t = asyncio.create_task(
        request_ea_rates("nX", "eurusd", "H1", count=300))
    await asyncio.sleep(0.01)
    cmds = registry.drain_commands("nX")
    assert cmds[0]["type"] == "rates_req"
    assert cmds[0]["params"] == {"symbol": "EURUSD", "timeframe": "H1",
                                 "count": 300}
    registry.resolve_result("nX", cmds[0]["req_id"], True,
                            {"bars": [[1, 2, 3, 4, 5]]}, None)
    out = await t
    assert out["ok"] is True
    assert out["payload"] == {"bars": [[1, 2, 3, 4, 5]]}

    t2 = asyncio.create_task(
        request_ea_rates("nX", "EURUSD", "H1", start=100, end=200))
    await asyncio.sleep(0.01)
    cmds2 = registry.drain_commands("nX")
    assert cmds2[0]["params"] == {"symbol": "EURUSD", "timeframe": "H1",
                                  "start": 100, "end": 200}

    t3 = asyncio.create_task(
        request_ea_rates("nX", "EURUSD", "H1", count=10**9))
    await asyncio.sleep(0.01)
    cmds3 = registry.drain_commands("nX")
    assert cmds3[0]["params"]["count"] == EA_RATES_MAX_BARS
    registry.resolve_result("nX", cmds3[0]["req_id"], True, {}, None)
    await asyncio.gather(t2, t3)
    registry.reset()


def test_paper_start_ea_offline_fails_fast(client):
    """EA 未上线（无 sync 活跃证明）→ GATEWAY_OFFLINE，不触碰命令通道。"""
    from app.services.paper.manager import PaperManager

    registry.reset()
    token = _register(client)["Authorization"].split(" ")[1]
    acct_id, _ = _seed_ea_account(client, token)
    run_id = _seed_run(client, token, acct_id)

    client.portal.call(PaperManager().start, run_id)
    row = _get_run(client, run_id)
    assert row["status"] == "failed"
    assert row["error_code"] == "GATEWAY_OFFLINE"
    assert "未上线" in row["error_message"]
    registry.reset()


def test_paper_start_ea_timeout_fails_fast(client, monkeypatch):
    """EA 在线但不应答 rates_req → RESULT_TIMEOUT → GATEWAY_TIMEOUT。"""
    from app.services.paper.manager import PaperManager

    registry.reset()
    monkeypatch.setattr(settings, "EA_RESULT_TIMEOUT", 0.05)
    token = _register(client)["Authorization"].split(" ")[1]
    acct_id, node_id = _seed_ea_account(client, token)
    run_id = _seed_run(client, token, acct_id)

    async def _touch():
        import time as _time

        registry.touch(node_id, _time.time())
    client.portal.call(_touch)
    client.portal.call(PaperManager().start, run_id)

    row = _get_run(client, run_id)
    assert row["status"] == "failed"
    assert row["error_code"] == "GATEWAY_TIMEOUT"
    assert "超时" in row["error_message"]
    registry.reset()


def test_paper_start_ea_success_spawns_runner(client, monkeypatch):
    """假 EA 全链路：rates RPC → BarArrays → payload → runner spawn（假 sup）。"""
    from types import SimpleNamespace

    from app.services.backtest.supervisor import TERM_COMPLETED
    from app.services.paper import manager as paper_manager_mod
    from app.services.paper.manager import PaperManager

    registry.reset()
    token = _register(client)["Authorization"].split(" ")[1]
    acct_id, node_id = _seed_ea_account(client, token)
    run_id = _seed_run(client, token, acct_id, warmup_bars=5)

    bars = _bars(120)  # > need=106 → 验证「最新 N 根」裁剪
    resp_task = _spawn_fake_ea(client, node_id, bars, _EA_INFO)

    class _FakeSup:
        # 吞掉构造参数（stall_timeout 等）：这里只关心 manager 的启动序列。
        def __init__(self, rid, directory, module=None, **_kw):
            pass

        async def run(self, on_event, timeout=None):
            return SimpleNamespace(outcome=TERM_COMPLETED)

        async def send(self, cmd):
            return True

        def request_cancel(self):
            pass

    monkeypatch.setattr(paper_manager_mod, "RunnerSupervisor", _FakeSup)

    async def _touch():
        import time as _time

        registry.touch(node_id, _time.time())
    client.portal.call(_touch)  # 假 EA「上线」，否则 GATEWAY_OFFLINE 先触发
    client.portal.call(PaperManager().start, run_id)

    row = _wait_run_status(client, run_id, "stopped")
    assert row["status"] == "stopped"
    # need = warmup 5 + PAPER_HISTORY_BARS 100 + 1 → 最新 N 根裁剪生效
    assert row["snapshot"]["history_bars"] == (
        5 + paper_manager_mod.settings.PAPER_HISTORY_BARS + 1)
    assert row["snapshot"]["symbol_info"]["digits"] == 5
    client.portal.call(resp_task.cancel)  # 在 portal loop 上取消常驻假 EA
    registry.reset()


async def test_paper_poll_ea_branch_uses_rates_rpc(monkeypatch):
    """假 EA：poll 补数走 rates RPC 区间请求 → 已收盘 bar 送 runner。"""
    import contextlib
    import time as _time

    from app.config import settings as cfg
    from app.services.paper.manager import PaperManager

    registry.reset()
    monkeypatch.setattr(cfg, "PAPER_POLL_SECONDS", 0.01)
    mgr = PaperManager()
    last_ts = int(_time.time()) - 7200  # 两小时前（H1 → 有已收盘 bar 可补）
    h = _mk_ea_handle()
    h.last_ts = last_ts
    h.tf_seconds = 3600
    h.timeframe = "H1"
    mgr._handles["r-ea"] = h

    async def _responder():
        while True:
            for cmd in registry.drain_commands("n-ea"):
                if cmd["type"] == "rates_req":
                    assert cmd["params"]["start"] == last_ts + 1
                    registry.resolve_result("n-ea", cmd["req_id"], True, {
                        "bars": [[last_ts + 3600, 1.1, 1.2, 1.0, 1.15, 10, 1]],
                        "symbol_info": {},
                    }, None)
            await asyncio.sleep(0.005)

    responder = asyncio.create_task(_responder())
    task = asyncio.create_task(mgr._poll("r-ea"))
    try:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(_until(lambda: len(h.sup.sent) == 1), 2.0)
        assert h.sup.sent[0]["cmd"] == "bars"
        assert h.sup.sent[0]["rows"] == [
            [last_ts + 3600, 1.1, 1.2, 1.0, 1.15, 10, 1]]
        assert h.last_ts == last_ts + 3600
    finally:
        for t in (task, responder):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
    registry.reset()


def test_ea_account_mirror_writes_run_row(client):
    """账户镜像：registry.last_account → _write_account 同构逐字段落库。"""

    from app.services.paper.manager import PaperManager

    registry.reset()
    token = _register(client)["Authorization"].split(" ")[1]
    acct_id, node_id = _seed_ea_account(client, token)
    run_id = _seed_run(client, token, acct_id, status="running")

    mgr = PaperManager()
    h = _mk_ea_handle()
    h.run_id = run_id
    h.ea_node_id = node_id
    h.last_ts = 1_700_000_000
    h.account = {}
    h.pending_equity = []
    h.last_account_write = 0.0
    mgr._handles[run_id] = h

    registry.set_account(node_id, {
        "balance": 5555.55, "equity": 5600.0, "margin_used": 10.0,
        "margin_free": 5545.55, "unrealized_pnl": 44.45, "margin_level": 560.0,
        "positions": [{
            "id": 123456, "symbol": "EURUSD", "direction": "buy",
            "volume": 0.1, "open_price": 1.10, "current_price": 1.11,
            "unrealized_pnl": 44.45, "realized_pnl": 0.0, "swap": 0.0,
            "commission": 0.0, "opened_at": 1_699_999_000,
        }],
    })
    client.portal.call(mgr._mirror_ea_account, run_id)
    row = _get_run(client, run_id)
    assert row["balance"] == 5555.55
    assert row["equity"] == 5600.0
    assert row["margin_used"] == 10.0
    assert row["unrealized_pnl"] == 44.45
    assert len(row["positions"]) == 1
    pos = row["positions"][0]
    assert pos["id"] == 123456 and pos["direction"] == "buy"
    assert pos["opened_at"].startswith("2023-11-14")  # UTC epoch → iso
    assert row["last_bar_time"] is not None  # 由 h.last_ts 注入
    assert len(row["equity_history"]) == 1

    # 签名未变 → 不重复写（equity_history 不再增长）
    client.portal.call(mgr._mirror_ea_account, run_id)
    row2 = _get_run(client, run_id)
    assert len(row2["equity_history"]) == 1
    mgr._handles.clear()
    registry.reset()


async def test_ea_run_ignores_runner_account_event():
    """EA run：runner 的模拟账户事件只做就绪门控，不写 run 行镜像。"""
    from app.services.paper.manager import PaperManager

    mgr = PaperManager()
    # 记录式桩：直接断言落库调用只发生在 python run 上
    writes: list[str] = []

    async def _record_write(rid: str) -> None:
        writes.append(rid)

    mgr._write_account = _record_write
    h = _mk_ea_handle()
    h.account = {}
    h.pending_equity = []
    h.last_account_write = 0.0
    h.ready = False
    mgr._handles["r-ea"] = h

    on_event = mgr._on_event("r-ea")
    await on_event({
        "event": "account", "balance": 999.0, "equity": 999.0,
        "last_bar_time": "2024-01-01T00:00:00+00:00",
    })
    assert h.account == {}          # 模拟账户不拥有 run 行
    assert h.pending_equity == []   # 不产生模拟净值点
    assert h.ready is True          # 就绪门控照常
    assert h.last_ts > 0            # 游标由 runner 账户事件推进

    # 非 EA run：行为不变
    h2 = _mk_ea_handle()
    h2.node_type = "python"
    h2.ea_node_id = ""
    h2.account = {}
    h2.pending_equity = []
    h2.last_account_write = 0.0
    mgr._handles["r-py"] = h2
    on_event2 = mgr._on_event("r-py")
    await on_event2({
        "event": "account", "balance": 888.0, "equity": 888.0,
        "last_bar_time": "2024-01-01T00:00:00+00:00",
    })
    assert h2.account["balance"] == 888.0
    assert len(h2.pending_equity) == 1
    assert writes == ["r-py"]       # 只有 python run 走 _write_account
    mgr._handles.clear()


# ── 模型/迁移三处同步 ─────────────────────────────────────────

def test_gateway_node_type_check_in_metadata():
    """迁移 CHECK 与模型元数据同步（防『测试全绿但真实库 500』漂移）。"""
    from app.models import GatewayNode

    checks = {
        c.name: str(c.sqltext)
        for c in GatewayNode.__table__.constraints
        if isinstance(c, CheckConstraint) and c.name
    }
    assert "ck_gateway_nodes_type" in checks
    assert "'python'" in checks["ck_gateway_nodes_type"]
    assert "'ea_bridge'" in checks["ck_gateway_nodes_type"]


# ── tick 链路：EA 缓冲 → runner（假 EA 单测）─────────────────

class _FakeSup:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.ok = True

    async def send(self, cmd: dict) -> bool:
        self.sent.append(cmd)
        return self.ok


def _mk_ea_handle() -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        run_id="r-ea", sup=_FakeSup(), symbol="EURUSD", gateway_url="",
        node_type="ea_bridge", ea_node_id="n-ea",
        paused=asyncio.Event(), stopping=False, child_live=True, ready=False,
        last_ts=0, tf_seconds=3600, timeframe="H1",
        last_tick_msc=1_000_000, has_on_tick=True, tick_task=None, busy=False,
        account={}, pending_equity=[], last_account_write=0.0,
        ea_account_sig=(), logs=[], trades=[],
    )


async def test_tick_poll_ea_branch_drains_and_dedups(monkeypatch):
    """假 EA：注册表缓冲 → _tick_poll → runner；游标去重 + 去重后不重发。"""
    from app.config import settings as cfg
    from app.services.paper.manager import PaperManager

    registry.reset()
    monkeypatch.setattr(cfg, "TICK_POLL_SECONDS", 0.01)
    mgr = PaperManager()
    h = _mk_ea_handle()
    mgr._handles["r-ea"] = h

    task = asyncio.create_task(mgr._tick_poll("r-ea"))
    try:
        # EA 首次 sync 上行（含一条早于游标的陈旧 tick）
        registry.push_ticks("n-ea", "EURUSD", [
            [900_000, 0.9, 0.9002], [1_500_000, 1.1, 1.1002]], max_rows=100)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(_until(lambda: len(h.sup.sent) == 1), 2.0)
        assert h.sup.sent == [{"cmd": "ticks", "rows": [[1_500_000, 1.1, 1.1002]]}]
        assert h.last_tick_msc == 1_500_000

        # EA 重发同游标 tick + 新 tick → 只发新行（游标去重）
        registry.push_ticks("n-ea", "EURUSD", [
            [1_500_000, 1.1, 1.1002], [1_700_000, 1.2, 1.2002]], max_rows=100)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(_until(lambda: len(h.sup.sent) == 2), 2.0)
        assert h.sup.sent[1] == {"cmd": "ticks", "rows": [[1_700_000, 1.2, 1.2002]]}
        assert h.last_tick_msc == 1_700_000
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    registry.reset()


async def _until(cond, timeout=2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if cond():
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("条件未在超时前满足")
