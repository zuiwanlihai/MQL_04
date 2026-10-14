"""Parameter optimization API tests (grid / random, PRD Phase 2 P1).

Grid runs execute real child backtests through the in-process queue with
parameters that actually change strategy behaviour — asserting that the
payload.parameters injection into ``ctx.params`` works end to end.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.services.backtest.analysis import expand_grid, sample_random
from app.services.gateway_client import GatewayClient

API = "/api/v1/backtests"
STRAT_API = "/api/v1/strategies"

TF_SECONDS = 3600
BASE_PRICE = 1.10000

SYMBOL_INFO = {
    "name": "EURUSD",
    "description": "Euro vs US Dollar",
    "digits": 5,
    "point": 0.00001,
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

# Long-biased strategy whose behaviour depends on declared params:
# ``threshold`` controls hold length (price trends up → longer = better),
# ``size`` scales the position.  Different combos MUST produce different
# reports — this proves the parameter override reaches the strategy.
STRATEGY_PARAM = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={"threshold": 2, "size": 0.1})

def on_bar(ctx, bar):
    pos = ctx.get_positions()
    if pos:
        if ctx.storage.get("hold", 0) >= ctx.params["threshold"]:
            ctx.close(pos[0].id)
            ctx.storage["hold"] = 0
        else:
            ctx.storage["hold"] = ctx.storage.get("hold", 0) + 1
    else:
        ctx.buy("EURUSD", ctx.params["size"])

def on_order(ctx, order):
    pass
"""


@pytest.fixture()
def gw(monkeypatch):
    """Deterministic rising bars straight from the GatewayClient class."""
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


@pytest.fixture
def token(client: TestClient) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"opt-{uuid.uuid4().hex[:10]}@test.com",
            "username": f"opt{uuid.uuid4().hex[:10]}",
            "password": "Passw0rd!",
            "full_name": "优化测试",
        },
    )
    assert r.status_code == 201
    return r.json()["access_token"]


@pytest.fixture
def strategy(client: TestClient, token: str):
    r = client.post(
        STRAT_API,
        json={"name": f"opt-{uuid.uuid4().hex[:8]}", "timeframe": "H1"},
        headers=_auth(token),
    )
    assert r.status_code == 201
    sid = r.json()["id"]
    resp = client.put(
        f"{STRAT_API}/{sid}/source",
        json={"source_code": STRATEGY_PARAM},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return {"id": sid}


def _create(gw, client, token, strategy, hours=24):
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
    gw["set_bars"](start, hours + 2)
    resp = client.post(API, json=body, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()[0]


def _wait_terminal(client, token, task_id, timeout=90.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"{API}/{task_id}", headers=_auth(token))
        assert r.status_code == 200
        if r.json()["status"] in ("completed", "failed", "cancelled"):
            return r.json()
        time.sleep(0.3)
    raise AssertionError(f"任务 {task_id} 超时未进入终态")


def _wait_analysis(client, token, analysis_id, timeout=120.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"{API}/analyses/{analysis_id}", headers=_auth(token))
        assert r.status_code == 200, r.text
        if r.json()["status"] in ("completed", "failed"):
            return r.json()
        time.sleep(0.5)
    raise AssertionError(f"分析 {analysis_id} 超时未进入终态")


# ── Pure helpers ───────────────────────────────────────────────

def test_expand_grid_row_major_order():
    combos = expand_grid([("a", [1, 2]), ("b", ["x", "y", "z"])])
    assert combos == [
        {"a": 1, "b": "x"},
        {"a": 1, "b": "y"},
        {"a": 1, "b": "z"},
        {"a": 2, "b": "x"},
        {"a": 2, "b": "y"},
        {"a": 2, "b": "z"},
    ]


def test_sample_random_seeded_and_reproducible():
    space = [("p", [1, 2, 3, 4, 5])]
    s1 = sample_random(space, 3, seed=42)
    s2 = sample_random(space, 3, seed=42)
    assert s1 == s2
    assert len(s1) == 3
    values = {c["p"] for c in s1}
    assert len(values) == 3  # without replacement


def test_sample_random_oversample_shuffles_full_grid():
    space = [("p", [1, 2, 3])]
    out = sample_random(space, 10, seed=7)
    assert sorted(c["p"] for c in out) == [1, 2, 3]


def test_model_kind_constraint_allows_optimization():
    """The kind CHECK constraint in metadata must match the migrated DB —
    a drift here passes create_all tests but breaks real deployments
    (CheckViolation → HTTP 500 on POST /optimize)."""
    from sqlalchemy import CheckConstraint

    from app.models.analysis import BacktestAnalysis

    kind_ck = next(
        c
        for c in BacktestAnalysis.__table_args__
        if isinstance(c, CheckConstraint) and c.name == "ck_backtest_analyses_kind"
    )
    assert "optimization" in str(kind_ck.sqltext)


# ── API ────────────────────────────────────────────────────────

def test_opt_requires_auth(client):
    assert client.post(f"{API}/{uuid.uuid4()}/optimize", json={}).status_code == 401


def test_opt_requires_completed_task_409(gw, client, token, strategy, monkeypatch):
    from app.api.v1.endpoints import backtests as bt_mod

    monkeypatch.setattr(bt_mod.settings, "BACKTEST_ENABLED", False)
    task = _create(gw, client, token, strategy)
    assert task["status"] == "pending"
    r = client.post(
        f"{API}/{task['id']}/optimize",
        json={"space": [{"name": "threshold", "values": [2, 4]}]},
        headers=_auth(token),
    )
    assert r.status_code == 409
    assert "已完成" in r.json()["detail"]


def test_opt_validation_422(gw, client, token, strategy):
    task = _create(gw, client, token, strategy)
    assert _wait_terminal(client, token, task["id"])["status"] == "completed"
    hdr = _auth(token)

    # duplicate parameter names
    r = client.post(
        f"{API}/{task['id']}/optimize",
        json={"space": [
            {"name": "a", "values": [1, 2]},
            {"name": "a", "values": [3, 4]},
        ]},
        headers=hdr,
    )
    assert r.status_code == 422
    assert "重复" in str(r.json()["detail"])

    # grid total > 100 (3 params × 5 values = 125)
    r = client.post(
        f"{API}/{task['id']}/optimize",
        json={"space": [{"name": "a", "values": [1, 2, 3, 4, 5]},
                        {"name": "b", "values": [1, 2, 3, 4, 5]},
                        {"name": "c", "values": [1, 2, 3, 4, 5]}]},
        headers=hdr,
    )
    assert r.status_code == 422
    assert "上限 100" in str(r.json()["detail"])

    # random samples > total combos
    r = client.post(
        f"{API}/{task['id']}/optimize",
        json={"space": [{"name": "a", "values": [1, 2, 3]}],
              "method": "random", "samples": 4},
        headers=hdr,
    )
    assert r.status_code == 422
    assert "超过组合总数" in str(r.json()["detail"])

    # invalid identifier
    r = client.post(
        f"{API}/{task['id']}/optimize",
        json={"space": [{"name": "1bad", "values": [1, 2]}]},
        headers=hdr,
    )
    assert r.status_code == 422

    # single candidate value
    r = client.post(
        f"{API}/{task['id']}/optimize",
        json={"space": [{"name": "a", "values": [1]}]},
        headers=hdr,
    )
    assert r.status_code == 422


def test_opt_grid_lifecycle_and_ranking(gw, client, token, strategy):
    task = _create(gw, client, token, strategy)
    assert _wait_terminal(client, token, task["id"])["status"] == "completed"

    r = client.post(
        f"{API}/{task['id']}/optimize",
        json={
            "space": [
                {"name": "threshold", "values": [1, 5]},
                {"name": "size", "values": [0.1, 0.2]},
            ],
            "method": "grid",
            "objective": "total_return_percent",
        },
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    ana = r.json()
    assert ana["kind"] == "optimization"
    assert ana["status"] == "running"
    children = ana["params"]["children"]
    assert len(children) == 4
    assert ana["params"]["samples"] == 4

    # Child tasks merge the combo into the source baseline parameters.
    for m in children:
        rr = client.get(f"{API}/{m['task_id']}", headers=_auth(token))
        assert rr.status_code == 200
        body = rr.json()
        assert body["parameters"] == m["params"]
        snap = body["config_snapshot"]
        assert snap["analysis_id"] == ana["id"]
        assert snap["analysis_role"] == f"opt_{m['index']}"

    done = _wait_analysis(client, token, ana["id"])
    assert done["status"] == "completed", done.get("error")
    res = done["result"]
    assert res["method"] == "grid"
    assert res["objective"] == "total_return_percent"
    assert len(res["rows"]) == 4

    # Parameter injection actually changed behaviour: rising market means
    # threshold=5 rows must outperform their threshold=1 counterparts.
    by_combo = {tuple(sorted(m["params"].items())): m for m in res["rows"]}
    assert len(by_combo) == 4
    rets = {m["params"]["threshold"]: m["objective"] for m in res["rows"]
            if m["params"]["size"] == 0.1}
    assert rets[5] > rets[1]

    # Descending order by objective + best alias.
    objs = [m["objective"] for m in res["rows"]]
    assert objs == sorted(objs, reverse=True)
    assert res["best"]["objective"] == objs[0]


def test_opt_random_seeded_reproducible(gw, client, token, strategy, monkeypatch):
    from app.api.v1.endpoints import backtests as bt_mod

    task = _create(gw, client, token, strategy)
    assert _wait_terminal(client, token, task["id"])["status"] == "completed"
    monkeypatch.setattr(bt_mod.settings, "BACKTEST_ENABLED", False)

    body = {
        "space": [
            {"name": "threshold", "values": [1, 2, 3, 4, 5]},
            {"name": "size", "values": [0.1, 0.2]},
        ],
        "method": "random",
        "samples": 5,
        "seed": 123,
    }
    r1 = client.post(f"{API}/{task['id']}/optimize", json=body, headers=_auth(token))
    r2 = client.post(f"{API}/{task['id']}/optimize", json=body, headers=_auth(token))
    assert r1.status_code == r2.status_code == 201
    c1 = [m["params"] for m in r1.json()["params"]["children"]]
    c2 = [m["params"] for m in r2.json()["params"]["children"]]
    assert len(c1) == 5
    assert sorted(map(str, c1)) == sorted(map(str, c2))

    # Aggregation stays pending (queue disabled) — read returns running.
    r = client.get(f"{API}/analyses/{r1.json()['id']}", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["status"] == "running"


def test_opt_cross_user_404(gw, client, token, strategy, monkeypatch):
    from app.api.v1.endpoints import backtests as bt_mod

    task = _create(gw, client, token, strategy)
    assert _wait_terminal(client, token, task["id"])["status"] == "completed"
    monkeypatch.setattr(bt_mod.settings, "BACKTEST_ENABLED", False)
    r = client.post(
        f"{API}/{task['id']}/optimize",
        json={"space": [{"name": "threshold", "values": [1, 2]}]},
        headers=_auth(token),
    )
    assert r.status_code == 201
    ana_id = r.json()["id"]
    other = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"opt2-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"opt2{uuid.uuid4().hex[:8]}",
            "password": "Passw0rd!",
            "full_name": "越权测试",
        },
    ).json()["access_token"]
    assert client.get(f"{API}/analyses/{ana_id}", headers=_auth(other)).status_code == 404
