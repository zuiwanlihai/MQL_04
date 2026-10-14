"""Overfit-guard analyses API tests (Walk-Forward / Monte Carlo).

Monte Carlo runs synchronously against the trades of a real completed
backtest; Walk-Forward spawns real child tasks that the in-process queue
executes against the class-level faked gateway surface.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.services.gateway_client import GatewayClient

API = "/api/v1/backtests"
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

# Opens + closes repeatedly → enough closed trades for Monte Carlo.
STRATEGY_MULTI = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_bar(ctx, bar):
    pos = ctx.get_positions()
    if pos:
        if ctx.storage.get("hold", 0) >= 1:
            ctx.close(pos[0].id)
            ctx.storage["hold"] = 0
        else:
            ctx.storage["hold"] = 1
    else:
        ctx.buy("EURUSD", 0.1)

def on_order(ctx, order):
    pass
"""

STRATEGY_SINGLE = """
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
    """Serve deterministic bars straight from the GatewayClient class."""
    window: dict[str, object] = {}

    async def fake_symbols(self) -> list[dict]:
        return [dict(SYMBOL_INFO)]

    async def fake_rates_range(self, name, timeframe, start, end, limit=5000):
        rows = window.get("rows") or []
        return [r for r in rows if start <= int(r["time"]) < end]

    def set_bars(start: datetime, n: int) -> None:
        t0 = int(start.timestamp())
        rows = []
        for i in range(n):
            close = BASE_PRICE + i * 0.00005
            rows.append({
                "time": t0 + i * TF_SECONDS,
                "open": close - 0.00001,
                "high": close + 0.00002,
                "low": close - 0.00002,
                "close": close,
                "tick_volume": 100,
                "spread": 20,
            })
        window["rows"] = rows

    monkeypatch.setattr(GatewayClient, "symbols", fake_symbols)
    monkeypatch.setattr(GatewayClient, "rates_range", fake_rates_range)
    window["set_bars"] = set_bars
    yield window


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _audit_actions(client: TestClient, target_id: str) -> list[str]:
    """该目标上留下的审计动作（按写入顺序）。"""

    async def _q() -> list[str]:
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog

        async with AsyncSessionLocal() as db:
            return list((await db.execute(
                select(AuditLog.action).where(AuditLog.target_id == target_id)
                .order_by(AuditLog.seq)
            )).scalars().all())

    return client.portal.call(_q)


@pytest.fixture
def token(client: TestClient) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"ana-{uuid.uuid4().hex[:10]}@test.com",
            "username": f"ana{uuid.uuid4().hex[:10]}",
            "password": "Passw0rd!",
            "full_name": "分析测试",
        },
    )
    assert r.status_code == 201
    return r.json()["access_token"]


@pytest.fixture
def strategy(client: TestClient, token: str):
    """Strategy id + a source-code setter for switching trade cadence."""
    r = client.post(
        STRAT_API,
        json={"name": f"ana-{uuid.uuid4().hex[:8]}", "timeframe": "H1"},
        headers=_auth(token),
    )
    assert r.status_code == 201
    sid = r.json()["id"]

    def set_source(src: str) -> None:
        resp = client.put(
            f"{STRAT_API}/{sid}/source", json={"source_code": src},
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text

    set_source(STRATEGY_MULTI)
    return {"id": sid, "set_source": set_source}


def _create(gw, client, token, strategy, hours=24, **kw):
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=hours)
    body = {
        "strategy_id": strategy["id"],
        "version": None,
        "symbol": "EURUSD",
        "timeframe": "H1",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "initial_deposit": 10000.0,
        "leverage": 100,
        "execution_mode": "standard",
        "spread_mode": "current",
        "warmup_bars": 0,
        "parameters": {},
        "assumption_ack": True,
        "sensitivity": False,
    }
    body.update(kw)
    n = hours + 2
    gw["set_bars"](start, n)
    resp = client.post(API, json=body, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()[0], (start, end)


def _wait_terminal(client, token, task_id, timeout=90.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"{API}/{task_id}", headers=_auth(token))
        assert r.status_code == 200
        if r.json()["status"] in ("completed", "failed", "cancelled"):
            return r.json()
        time.sleep(0.3)
    raise AssertionError(f"任务 {task_id} 超时未进入终态")


def _wait_analysis(client, token, analysis_id, timeout=90.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"{API}/analyses/{analysis_id}", headers=_auth(token))
        assert r.status_code == 200, r.text
        if r.json()["status"] in ("completed", "failed"):
            return r.json()
        time.sleep(0.5)
    raise AssertionError(f"分析 {analysis_id} 超时未进入终态")


# ── Monte Carlo ────────────────────────────────────────────────

def test_mc_requires_auth(client):
    assert client.get(f"{API}/analyses").status_code == 401


def test_mc_requires_completed_task_409(gw, client, token, strategy, monkeypatch):
    from app.api.v1.endpoints import backtests as bt_mod

    monkeypatch.setattr(bt_mod.settings, "BACKTEST_ENABLED", False)
    task, _ = _create(gw, client, token, strategy)
    assert task["status"] == "pending"
    r = client.post(
        f"{API}/{task['id']}/monte-carlo",
        json={"simulations": 500, "method": "bootstrap", "seed": 42},
        headers=_auth(token),
    )
    assert r.status_code == 409
    assert "已完成" in r.json()["detail"]


def test_mc_bootstrap_percentiles_and_probabilities(gw, client, token, strategy):
    task, _ = _create(gw, client, token, strategy)
    final = _wait_terminal(client, token, task["id"])
    assert final["status"] == "completed", final["error_message"]
    report = client.get(f"{API}/{task['id']}/report", headers=_auth(token)).json()["report"]

    r = client.post(
        f"{API}/{task['id']}/monte-carlo",
        json={"simulations": 500, "method": "bootstrap", "ruin_dd_percent": 30, "seed": 42},
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["kind"] == "monte_carlo"
    assert a["status"] == "completed"
    res = a["result"]
    assert res["method"] == "bootstrap"
    assert res["simulations"] == 500
    assert res["seed"] == 42
    assert res["original"]["final_return_percent"] == pytest.approx(
        report["total_return_percent"], abs=1e-6
    )
    assert res["original"]["trades"] == report["total_trades"]
    fr = res["final_return_percent"]
    assert fr["p5"] <= fr["p25"] <= fr["p50"] <= fr["p75"] <= fr["p95"]
    dd = res["max_dd_percent"]
    assert dd["p5"] <= dd["p95"]
    assert 0.0 <= res["prob_loss"] <= 1.0
    assert 0.0 <= res["prob_ruin"] <= 1.0


def test_mc_shuffle_keeps_final_return_exact(gw, client, token, strategy):
    """Permuting order preserves the trade set → final return is invariant."""
    task, _ = _create(gw, client, token, strategy)
    assert _wait_terminal(client, token, task["id"])["status"] == "completed"
    report = client.get(f"{API}/{task['id']}/report", headers=_auth(token)).json()["report"]

    r = client.post(
        f"{API}/{task['id']}/monte-carlo",
        json={"simulations": 300, "method": "shuffle", "seed": 7},
        headers=_auth(token),
    )
    assert r.status_code == 201
    fr = r.json()["result"]["final_return_percent"]
    orig = report["total_return_percent"]
    for k in ("p5", "p50", "p95"):
        assert fr[k] == pytest.approx(orig, abs=0.01), k


def test_mc_seed_reproducible(gw, client, token, strategy):
    task, _ = _create(gw, client, token, strategy)
    assert _wait_terminal(client, token, task["id"])["status"] == "completed"
    body = {"simulations": 300, "method": "bootstrap", "seed": 99}
    r1 = client.post(f"{API}/{task['id']}/monte-carlo", json=body, headers=_auth(token))
    r2 = client.post(f"{API}/{task['id']}/monte-carlo", json=body, headers=_auth(token))
    assert r1.status_code == r2.status_code == 201
    assert r1.json()["result"] == r2.json()["result"]


def test_mc_too_few_trades_422(gw, client, token, strategy):
    strategy["set_source"](STRATEGY_SINGLE)
    task, _ = _create(gw, client, token, strategy)
    assert _wait_terminal(client, token, task["id"])["status"] == "completed"
    r = client.post(
        f"{API}/{task['id']}/monte-carlo",
        json={"simulations": 300, "seed": 1},
        headers=_auth(token),
    )
    assert r.status_code == 422
    assert "至少 5 笔" in r.json()["detail"]


def test_mc_simulations_out_of_range_422(gw, client, token, strategy):
    task, _ = _create(gw, client, token, strategy)
    assert _wait_terminal(client, token, task["id"])["status"] == "completed"
    r = client.post(
        f"{API}/{task['id']}/monte-carlo",
        json={"simulations": 50, "seed": 1},
        headers=_auth(token),
    )
    assert r.status_code == 422


def test_mc_cross_user_404(gw, client, token, strategy):
    task, _ = _create(gw, client, token, strategy)
    assert _wait_terminal(client, token, task["id"])["status"] == "completed"
    other = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"ana2-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"ana2{uuid.uuid4().hex[:8]}",
            "password": "Passw0rd!",
            "full_name": "越权测试",
        },
    ).json()["access_token"]
    r = client.post(
        f"{API}/{task['id']}/monte-carlo",
        json={"simulations": 300, "seed": 1},
        headers=_auth(other),
    )
    assert r.status_code == 404
    assert client.get(f"{API}/analyses", headers=_auth(other)).status_code == 200


# ── Walk-Forward ───────────────────────────────────────────────

def test_wf_requires_completed_task_409(gw, client, token, strategy, monkeypatch):
    from app.api.v1.endpoints import backtests as bt_mod

    monkeypatch.setattr(bt_mod.settings, "BACKTEST_ENABLED", False)
    task, _ = _create(gw, client, token, strategy)
    assert task["status"] == "pending"
    r = client.post(
        f"{API}/{task['id']}/walk-forward",
        json={"folds": 2, "mode": "rolling"},
        headers=_auth(token),
    )
    assert r.status_code == 409
    assert "已完成" in r.json()["detail"]


def test_wf_folds_out_of_range_422(gw, client, token, strategy):
    task, _ = _create(gw, client, token, strategy)
    assert _wait_terminal(client, token, task["id"])["status"] == "completed"
    r = client.post(
        f"{API}/{task['id']}/walk-forward",
        json={"folds": 1, "mode": "rolling"},
        headers=_auth(token),
    )
    assert r.status_code == 422


def test_wf_lifecycle_and_aggregation(gw, client, token, strategy):
    task, (start, end) = _create(gw, client, token, strategy)
    assert _wait_terminal(client, token, task["id"])["status"] == "completed"

    r = client.post(
        f"{API}/{task['id']}/walk-forward",
        json={"folds": 2, "mode": "rolling"},
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    ana = r.json()
    assert ana["kind"] == "walk_forward"
    assert ana["status"] == "running"
    children = ana["params"]["children"]
    assert len(children) == 4  # 2 folds × (IS + OOS)

    # Rolling windows: fold i → IS=seg i, OOS=seg i+1 (3 equal segments).
    span = (end - start).total_seconds()
    seg = span / 3
    fold1_is = next(c for c in children if c["fold"] == 1 and c["role"] == "is")
    fold1_oos = next(c for c in children if c["fold"] == 1 and c["role"] == "oos")
    fold2_oos = next(c for c in children if c["fold"] == 2 and c["role"] == "oos")
    assert datetime.fromisoformat(fold1_is["start"]) == start
    assert datetime.fromisoformat(fold1_oos["end"]) == end - timedelta(seconds=seg)
    assert datetime.fromisoformat(fold2_oos["end"]) == end

    # Children are real backtest tasks owned by the same user.
    for c in children:
        rr = client.get(f"{API}/{c['task_id']}", headers=_auth(token))
        assert rr.status_code == 200
        snap = rr.json()["config_snapshot"]
        assert snap["analysis_id"] == ana["id"]
        assert snap["analysis_role"] == f"wf_{c['role']}_{c['fold']}"

    done = _wait_analysis(client, token, ana["id"])
    assert done["status"] == "completed", done.get("error")
    result = done["result"]
    assert result["mode"] == "rolling"
    assert len(result["rows"]) == 2
    for row in result["rows"]:
        assert row["is_task_id"] and row["oos_task_id"]
        assert row["oos_trades"] >= 0
    summary = result["summary"]
    assert summary["oos_profitable"] <= 2
    assert summary["oos_median_return_percent"] is not None
    assert isinstance(summary["robust"], bool)

    # Analysis list shows the aggregated row.
    lst = client.get(f"{API}/analyses?task_id={task['id']}", headers=_auth(token))
    assert lst.status_code == 200
    kinds = {i["kind"] for i in lst.json()["items"]}
    assert "walk_forward" in kinds
    assert lst.json()["items"][0]["status"] == "completed"

    # 审计留痕：创建分析是事后补记，行必须真的落库（写进请求会话没人 commit
    # 的话，接口照样 201，库里却查不到谁拆过这段回测）。
    assert _audit_actions(client, ana["id"]) == ["backtest.walk_forward_create"]


def test_wf_anchored_is_starts_at_range_start(gw, client, token, strategy, monkeypatch):
    """Anchored mode: every IS window starts at the full-range start.

    The queue is disabled *after* the source task completes so the spawned
    children stay pending — window shape is asserted without execution.
    """
    task, (start, end) = _create(gw, client, token, strategy)
    assert _wait_terminal(client, token, task["id"])["status"] == "completed"
    from app.api.v1.endpoints import backtests as bt_mod

    monkeypatch.setattr(bt_mod.settings, "BACKTEST_ENABLED", False)
    r = client.post(
        f"{API}/{task['id']}/walk-forward",
        json={"folds": 3, "mode": "anchored"},
        headers=_auth(token),
    )
    assert r.status_code == 201
    children = r.json()["params"]["children"]
    assert len(children) == 6
    for c in children:
        if c["role"] == "is":
            assert datetime.fromisoformat(c["start"]) == start
    # IS end of fold 1 == OOS start of fold 1.
    is1 = next(c for c in children if c["fold"] == 1 and c["role"] == "is")
    oos1 = next(c for c in children if c["fold"] == 1 and c["role"] == "oos")
    assert is1["end"] == oos1["start"]


def test_wf_cross_user_analysis_404(gw, client, token, strategy, monkeypatch):
    task, _ = _create(gw, client, token, strategy)
    assert _wait_terminal(client, token, task["id"])["status"] == "completed"
    from app.api.v1.endpoints import backtests as bt_mod

    monkeypatch.setattr(bt_mod.settings, "BACKTEST_ENABLED", False)
    r = client.post(
        f"{API}/{task['id']}/walk-forward",
        json={"folds": 2, "mode": "rolling"},
        headers=_auth(token),
    )
    assert r.status_code == 201
    ana_id = r.json()["id"]
    other = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"ana3-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"ana3{uuid.uuid4().hex[:8]}",
            "password": "Passw0rd!",
            "full_name": "越权测试",
        },
    ).json()["access_token"]
    assert client.get(f"{API}/analyses/{ana_id}", headers=_auth(other)).status_code == 404
