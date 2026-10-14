"""Portfolio backtest API tests (multi-symbol, PRD Phase 3 P2).

Pure helpers (curve merge / metrics) are tested without a database; the
lifecycle tests run real per-symbol child tasks through the in-process
queue against the class-level faked gateway surface, then exercise the
compute-on-read aggregation via GET /portfolios/{id}.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.services.backtest.portfolio import (
    compute_portfolio_metrics,
    merge_equity_curves,
)
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

STRATEGY_TRADE = """
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
        # ctx.symbol — NOT the "EURUSD" literal.  A portfolio child runs this
        # same strategy once per symbol; the runtime overrides the declared
        # symbol with the child's task symbol, so a hard-coded literal is
        # rejected with INVALID_SYMBOL and that child silently trades nothing
        # (which is what made this test's two children disagree).
        ctx.buy(ctx.symbol, 0.1)

def on_order(ctx, order):
    pass
"""


@pytest.fixture()
def gw(monkeypatch):
    """Deterministic bars served to every symbol from the GatewayClient."""
    window: dict[str, object] = {}

    async def fake_symbols(self) -> list[dict]:
        infos = [dict(SYMBOL_INFO)]
        if window.get("extra"):
            second = dict(SYMBOL_INFO)
            second["name"] = "GBPUSD"
            infos.append(second)
        return infos

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
    window["extra"] = False
    yield window


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(client: TestClient) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"pf-{uuid.uuid4().hex[:10]}@test.com",
            "username": f"pf{uuid.uuid4().hex[:10]}",
            "password": "Passw0rd!",
            "full_name": "组合测试",
        },
    )
    assert r.status_code == 201
    return r.json()["access_token"]


@pytest.fixture
def strategy(client: TestClient, token: str):
    r = client.post(
        STRAT_API,
        json={"name": f"pf-{uuid.uuid4().hex[:8]}", "timeframe": "H1"},
        headers=_auth(token),
    )
    assert r.status_code == 201
    sid = r.json()["id"]
    resp = client.put(
        f"{STRAT_API}/{sid}/source",
        json={"source_code": STRATEGY_TRADE},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return {"id": sid}


def _body(strategy_id: str, hours=24, **kw) -> dict:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=hours)
    body = {
        "strategy_id": strategy_id,
        "symbols": ["EURUSD", "GBPUSD"],
        "timeframe": "H1",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total_deposit": 10000.0,
        "leverage": 100,
        "execution_mode": "standard",
        "spread_mode": "current",
        "warmup_bars": 0,
        "assumption_ack": True,
    }
    body.update(kw)
    return body


def _create_portfolio(gw, client, token, strategy, hours=24, **kw):
    body = _body(strategy["id"], hours, **kw)
    gw["set_bars"](
        datetime.fromisoformat(body["start_date"]), hours + 2
    )
    resp = client.post(f"{API}/portfolios", json=body, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _wait_portfolio(client, token, portfolio_id, timeout=120.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"{API}/portfolios/{portfolio_id}", headers=_auth(token))
        assert r.status_code == 200, r.text
        if r.json()["status"] in ("completed", "failed"):
            return r.json()
        time.sleep(0.5)
    raise AssertionError(f"组合 {portfolio_id} 超时未进入终态")


# ── Pure helpers ───────────────────────────────────────────────

def test_merge_equity_curves_union_ffill_and_drawdown():
    curves = {
        "A": [
            {"time": "2025-01-01T00:00:00+00:00", "equity": 100.0,
             "balance": 100.0, "drawdown": 0.0, "baseline": 100.0},
            {"time": "2025-01-01T02:00:00+00:00", "equity": 110.0,
             "balance": 110.0, "drawdown": 0.0, "baseline": 100.0},
        ],
        "B": [
            {"time": "2025-01-01T01:00:00+00:00", "equity": 90.0,
             "balance": 90.0, "drawdown": 0.0, "baseline": 100.0},
            {"time": "2025-01-01T02:00:00+00:00", "equity": 80.0,
             "balance": 80.0, "drawdown": 0.0, "baseline": 100.0},
        ],
    }
    merged = merge_equity_curves(curves, {"A": 100.0, "B": 100.0})
    # Union timeline: 00:00, 01:00, 02:00.
    assert [p["time"] for p in merged] == [
        "2025-01-01T00:00:00+00:00",
        "2025-01-01T01:00:00+00:00",
        "2025-01-01T02:00:00+00:00",
    ]
    # 00:00: A=100, B ffilled at starting deposit 100 → 200.
    assert merged[0]["equity"] == 200.0
    # 01:00: A ffilled 100, B=90 → 190; drawdown from peak 200 = 5%.
    assert merged[1]["equity"] == 190.0
    assert merged[1]["drawdown"] == pytest.approx(5.0)
    # 02:00: A=110, B=80 → 190.
    assert merged[2]["equity"] == 190.0
    assert merged[2]["baseline"] == 200.0


def test_compute_portfolio_metrics_profit_and_drawdown():
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 2, tzinfo=UTC)
    equity = [
        {"time": "2025-01-01T00:00:00+00:00", "equity": 100.0},
        {"time": "2025-01-01T12:00:00+00:00", "equity": 90.0},
        {"time": "2025-01-02T00:00:00+00:00", "equity": 105.0},
    ]
    m = compute_portfolio_metrics(equity, 100.0, start, end)
    assert m["total_return"] == pytest.approx(5.0)
    assert m["total_return_percent"] == pytest.approx(5.0)
    assert m["max_drawdown_percent"] == pytest.approx(10.0)
    assert m["max_drawdown_amount"] == pytest.approx(10.0)
    assert m["annual_return_percent"] > 0
    assert m["sharpe_ratio"] != 0.0


def test_compute_portfolio_metrics_empty_curve():
    start = datetime(2025, 1, 1, tzinfo=UTC)
    m = compute_portfolio_metrics([], 100.0, start, start)
    assert m["total_return_percent"] == 0.0
    assert m["max_drawdown_percent"] == 0.0


# ── API ────────────────────────────────────────────────────────

def test_pf_requires_auth(client):
    assert client.get(f"{API}/portfolios").status_code == 401


def test_pf_validation_422(client, token, strategy):
    hdr = _auth(token)
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=24)
    base = _body(strategy["id"])
    base["start_date"], base["end_date"] = start.isoformat(), end.isoformat()

    # only one symbol
    r = client.post(f"{API}/portfolios", json={**base, "symbols": ["EURUSD"]},
                    headers=hdr)
    assert r.status_code == 422

    # duplicate symbols
    r = client.post(f"{API}/portfolios",
                    json={**base, "symbols": ["EURUSD", "EURUSD"]}, headers=hdr)
    assert r.status_code == 422
    assert "重复" in str(r.json()["detail"])

    # assumption not acknowledged
    r = client.post(f"{API}/portfolios", json={**base, "assumption_ack": False},
                    headers=hdr)
    assert r.status_code == 422

    # inverted window
    r = client.post(f"{API}/portfolios",
                    json={**base, "end_date": start.isoformat(),
                          "start_date": end.isoformat()}, headers=hdr)
    assert r.status_code == 422

    # unknown strategy
    r = client.post(f"{API}/portfolios",
                    json={**base, "strategy_id": str(uuid.uuid4())}, headers=hdr)
    assert r.status_code == 404

    # empty source
    client.put(f"{STRAT_API}/{strategy['id']}/source",
               json={"source_code": ""}, headers=hdr)
    r = client.post(f"{API}/portfolios", json=base, headers=hdr)
    assert r.status_code == 422
    assert "策略代码为空" in r.json()["detail"]


def test_pf_create_spawns_equal_deposit_children(
    gw, client, token, strategy, monkeypatch
):
    from app.api.v1.endpoints import backtests as bt_mod

    monkeypatch.setattr(bt_mod.settings, "BACKTEST_ENABLED", False)
    pf = _create_portfolio(gw, client, token, strategy)
    assert pf["status"] == "running"
    assert pf["strategy_name"]
    assert pf["symbols"] == ["EURUSD", "GBPUSD"]
    assert pf["total_deposit"] == 10000.0

    children = pf["params"]["children"]
    assert len(children) == 2
    for m in children:
        assert m["deposit"] == 5000.0
        rr = client.get(f"{API}/{m['task_id']}", headers=_auth(token))
        assert rr.status_code == 200
        body = rr.json()
        assert body["symbol"] == m["symbol"]
        assert float(body["initial_deposit"]) == 5000.0
        snap = body["config_snapshot"]
        assert snap["portfolio_id"] == pf["id"]
        assert snap["analysis_role"] == m["symbol"]

    # Children stay pending (queue disabled) → GET still returns running.
    r = client.get(f"{API}/portfolios/{pf['id']}", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["status"] == "running"


def test_pf_lifecycle_aggregation(gw, client, token, strategy):
    gw["extra"] = True  # GBPUSD becomes a real tradable symbol
    pf = _create_portfolio(gw, client, token, strategy)
    children = pf["params"]["children"]

    # Per-symbol child tasks appear in the normal task list.
    for m in children:
        rr = client.get(f"{API}/{m['task_id']}", headers=_auth(token))
        assert rr.status_code == 200

    done = _wait_portfolio(client, token, pf["id"])
    assert done["status"] == "completed", done.get("error")
    res = done["result"]
    assert res["equity_curve"], "combined equity curve must not be empty"
    m = res["metrics"]
    assert m["total_return_percent"] != 0.0
    assert m["max_drawdown_percent"] >= 0.0
    assert len(res["symbols"]) == 2
    by_symbol = {r["symbol"]: r for r in res["symbols"]}
    assert set(by_symbol) == {"EURUSD", "GBPUSD"}
    for row in res["symbols"]:
        assert row["task_id"]
        assert row["total_trades"] >= 0
    # Both children ran the same rising bars → same return profile.
    assert by_symbol["EURUSD"]["total_return_percent"] == pytest.approx(
        by_symbol["GBPUSD"]["total_return_percent"], abs=0.01
    )
    # Portfolio list shows the cached aggregate too.
    lst = client.get(f"{API}/portfolios", headers=_auth(token))
    assert lst.status_code == 200
    item = next(i for i in lst.json()["items"] if i["id"] == pf["id"])
    assert item["status"] == "completed"
    assert item["result"]["metrics"]["total_return_percent"] != 0.0


def test_pf_child_failure_propagates(gw, client, token, strategy):
    """A symbol the faked gateway does not know → child fails → portfolio failed."""
    gw["extra"] = False  # gateway only knows EURUSD
    pf = _create_portfolio(gw, client, token, strategy)
    done = _wait_portfolio(client, token, pf["id"])
    assert done["status"] == "failed"
    assert "GBPUSD" in (done["error"] or "")


def test_pf_cross_user_404(gw, client, token, strategy, monkeypatch):
    from app.api.v1.endpoints import backtests as bt_mod

    monkeypatch.setattr(bt_mod.settings, "BACKTEST_ENABLED", False)
    pf = _create_portfolio(gw, client, token, strategy)
    other = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"pf2-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"pf2{uuid.uuid4().hex[:8]}",
            "password": "Passw0rd!",
            "full_name": "越权测试",
        },
    ).json()["access_token"]
    assert client.get(
        f"{API}/portfolios/{pf['id']}", headers=_auth(other)
    ).status_code == 404
    assert client.get(f"{API}/portfolios", headers=_auth(other)).status_code == 200


def test_pf_route_order_not_captured_by_task_id(client, token, monkeypatch):
    """GET /backtests/portfolios must not fall into GET /backtests/{task_id}."""
    from app.api.v1.endpoints import backtests as bt_mod

    monkeypatch.setattr(bt_mod.settings, "BACKTEST_ENABLED", False)
    r = client.get(f"{API}/portfolios", headers=_auth(token))
    assert r.status_code == 200
    assert "items" in r.json()
