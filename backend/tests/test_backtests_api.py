"""Backtest center API integration tests (M4).

The queue worker instantiates ``GatewayClient`` directly (no FastAPI DI), so
the gateway surface is monkeypatched at class level to serve synthetic bars.
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


@pytest.fixture
def token(client: TestClient) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"bt-{uuid.uuid4().hex[:10]}@test.com",
            "username": f"bt{uuid.uuid4().hex[:10]}",
            "password": "Passw0rd!",
            "full_name": "回测测试",
        },
    )
    assert r.status_code == 201
    return r.json()["access_token"]


@pytest.fixture
def strategy(client: TestClient, token: str) -> str:
    r = client.post(
        STRAT_API,
        json={"name": f"bt-{uuid.uuid4().hex[:8]}", "timeframe": "H1"},
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


def _body(strategy_id: str, **kw) -> dict:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=24)
    base = {
        "strategy_id": strategy_id,
        "version": None,
        "symbol": "EURUSD",
        "timeframe": "H1",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "initial_deposit": 10000.0,
        "leverage": 100,
        "execution_mode": "standard",
        "spread_mode": "current",
        "warmup_bars": 5,
        "parameters": {},
        "assumption_ack": True,
        "sensitivity": False,
    }
    base.update(kw)
    return base, (start, end)


def _create(gw, client, token, strategy_id, **kw):
    body, (start, end) = _body(strategy_id, **kw)
    # bars covering the warm-up region too
    bars_start = start - timedelta(hours=body["warmup_bars"])
    n = int((end - bars_start).total_seconds() // TF_SECONDS) + 2
    gw["set_bars"](bars_start, n)
    return client.post(API, json=body, headers=_auth(token)), body


def _wait_terminal(client, token, task_id, timeout=90.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"{API}/{task_id}", headers=_auth(token))
        assert r.status_code == 200
        status = r.json()["status"]
        if status in ("completed", "failed", "cancelled"):
            return r.json()
        time.sleep(0.5)
    raise AssertionError(f"任务 {task_id} 超时未进入终态")


# ── validation ─────────────────────────────────────────────────

def test_requires_auth(client):
    assert client.get(API).status_code == 401


def test_missing_assumption_ack_422(gw, client, token, strategy):
    resp, _ = _create(gw, client, token, strategy, assumption_ack=False)
    assert resp.status_code == 422


def test_fixed_spread_requires_points_422(gw, client, token, strategy):
    resp, _ = _create(gw, client, token, strategy, spread_mode="fixed", spread_points=None)
    assert resp.status_code == 422


def test_sensitivity_with_optimistic_422(gw, client, token, strategy):
    resp, _ = _create(
        gw, client, token, strategy, execution_mode="optimistic", sensitivity=True
    )
    assert resp.status_code == 422


def test_start_after_end_422(gw, client, token, strategy):
    resp, _ = _create(gw, client, token, strategy,
                      start_date="2026-01-02T00:00:00+00:00",
                      end_date="2026-01-01T00:00:00+00:00")
    assert resp.status_code == 422


def test_empty_source_422(gw, client, token):
    r = client.post(
        STRAT_API, json={"name": f"e-{uuid.uuid4().hex[:8]}"}, headers=_auth(token)
    )
    sid = r.json()["id"]
    resp, _ = _create(gw, client, token, sid)
    assert resp.status_code == 422
    assert "代码为空" in resp.json()["detail"]


def test_unknown_strategy_404(gw, client, token):
    resp, _ = _create(gw, client, token, str(uuid.uuid4()))
    assert resp.status_code == 404


def test_archived_strategy_409(gw, client, token, strategy):
    client.patch(f"{STRAT_API}/{strategy}", json={"status": "archived"},
                 headers=_auth(token))
    resp, _ = _create(gw, client, token, strategy)
    assert resp.status_code == 409


def test_oversized_window_422(gw, client, token, strategy, monkeypatch):
    from app.api.v1.endpoints import backtests as bt_mod

    monkeypatch.setattr(bt_mod.settings, "BACKTEST_MAX_BARS", 1000)
    body, _ = _body(strategy)
    body["start_date"] = "2020-01-01T00:00:00+00:00"
    body["end_date"] = "2026-01-01T00:00:00+00:00"
    resp = client.post(API, json=body, headers=_auth(token))
    assert resp.status_code == 422
    assert "超过上限" in resp.json()["detail"]


# ── happy path ─────────────────────────────────────────────────

def test_create_returns_task_with_frozen_snapshot(gw, client, token, strategy):
    resp, body = _create(gw, client, token, strategy, execution_mode="conservative")
    assert resp.status_code == 201, resp.text
    items = resp.json()
    assert len(items) == 1
    task = items[0]
    assert task["execution_mode"] == "conservative"
    assert task["status"] in ("pending", "running", "completed")
    assert task["group_id"] is None
    detail = client.get(f"{API}/{task['id']}", headers=_auth(token)).json()
    snap = detail["config_snapshot"]
    assert snap["assumption_text"].startswith("保守档")
    assert snap["assumption_ack"] is True
    assert snap["source_hash"] and snap["seed"]
    assert snap["sdk"]["python"]


def test_full_lifecycle_report_trades_logs_csv(gw, client, token, strategy):
    resp, _ = _create(gw, client, token, strategy, execution_mode="standard",
                      warmup_bars=0)
    task_id = resp.json()[0]["id"]
    final = _wait_terminal(client, token, task_id)
    assert final["status"] == "completed", final["error_message"]
    assert final["progress_percent"] == 100

    rep = client.get(f"{API}/{task_id}/report", headers=_auth(token))
    assert rep.status_code == 200
    body = rep.json()
    report = body["report"]
    assert report["total_trades"] >= 1
    assert report["equity_curve"]
    pt = report["equity_curve"][0]
    assert set(pt) == {"time", "equity", "balance", "drawdown", "baseline"}

    trades = client.get(f"{API}/{task_id}/trades", headers=_auth(token)).json()
    assert trades["total"] >= 1
    t0 = trades["items"][0]
    assert t0["direction"] == "buy"
    assert t0["is_forced"] is True  # end-of-run close
    assert t0["base_price"] is not None

    logs = client.get(f"{API}/{task_id}/logs", headers=_auth(token)).json()
    assert logs["total"] >= 1
    assert logs["truncated"] is False
    kinds = {i["kind"] for i in logs["items"]}
    assert "signal" in kinds

    csv_resp = client.get(f"{API}/{task_id}/trades.csv", headers=_auth(token))
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert csv_resp.headers["content-disposition"].startswith("attachment")
    text = csv_resp.content.decode("utf-8-sig")
    assert text.startswith("\ufeff") or csv_resp.content.startswith(b"\xef\xbb\xbf")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines[0].startswith("ticket,direction,volume,open_time")
    assert len(lines) == 1 + trades["total"]


def test_log_kind_filter(gw, client, token, strategy):
    resp, _ = _create(gw, client, token, strategy, warmup_bars=0)
    tid = resp.json()[0]["id"]
    _wait_terminal(client, token, tid)
    r = client.get(f"{API}/{tid}/logs?kind=signal", headers=_auth(token)).json()
    assert all(i["kind"] == "signal" for i in r["items"])
    bad = client.get(f"{API}/{tid}/logs?kind=nope", headers=_auth(token))
    assert bad.status_code == 422


def test_report_before_completion_409(gw, client, token, strategy, monkeypatch):
    # Disable the queue so the task stays pending → report not generated.
    from app.api.v1.endpoints import backtests as bt_mod

    monkeypatch.setattr(bt_mod.settings, "BACKTEST_ENABLED", False)
    resp, _ = _create(gw, client, token, strategy)
    tid = resp.json()[0]["id"]
    r = client.get(f"{API}/{tid}/report", headers=_auth(token))
    assert r.status_code == 409
    # cancel a pending task → cancelled
    c = client.post(f"{API}/{tid}/cancel", headers=_auth(token))
    assert c.status_code == 200
    assert c.json()["status"] == "cancelled"


def test_cancel_terminal_409(gw, client, token, strategy, monkeypatch):
    from app.api.v1.endpoints import backtests as bt_mod

    monkeypatch.setattr(bt_mod.settings, "BACKTEST_ENABLED", False)
    resp, _ = _create(gw, client, token, strategy)
    tid = resp.json()[0]["id"]
    client.post(f"{API}/{tid}/cancel", headers=_auth(token))
    again = client.post(f"{API}/{tid}/cancel", headers=_auth(token))
    assert again.status_code == 409


# ── list / isolation ───────────────────────────────────────────

def test_list_filter_and_pagination(gw, client, token, strategy):
    for _ in range(3):
        assert _create(gw, client, token, strategy)[0].status_code == 201
    r = client.get(f"{API}?page=1&page_size=2", headers=_auth(token)).json()
    assert r["total"] >= 3
    assert len(r["items"]) == 2
    by_strategy = client.get(
        f"{API}?strategy_id={strategy}", headers=_auth(token)
    ).json()
    assert all(i["strategy_id"] == strategy for i in by_strategy["items"])
    bad = client.get(f"{API}?status=zzz", headers=_auth(token))
    assert bad.status_code == 422


def test_cross_user_access_404(gw, client, token, strategy):
    resp, _ = _create(gw, client, token, strategy)
    tid = resp.json()[0]["id"]
    other = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"bt2-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"bt2{uuid.uuid4().hex[:8]}",
            "password": "Passw0rd!",
            "full_name": "越权测试",
        },
    ).json()["access_token"]
    assert client.get(f"{API}/{tid}", headers=_auth(other)).status_code == 404
    assert client.get(f"{API}/{tid}/report", headers=_auth(other)).status_code == 404
    assert client.get(f"{API}/{tid}/trades", headers=_auth(other)).status_code == 404
    assert client.post(f"{API}/{tid}/cancel", headers=_auth(other)).status_code == 404


# ── sensitivity pairing ────────────────────────────────────────

def test_sensitivity_pair_and_compare(gw, client, token, strategy):
    resp, _ = _create(gw, client, token, strategy, execution_mode="conservative",
                      sensitivity=True)
    assert resp.status_code == 201
    items = resp.json()
    assert len(items) == 2
    modes = {i["execution_mode"] for i in items}
    assert modes == {"conservative", "standard"}
    gid = items[0]["group_id"]
    assert gid and items[1]["group_id"] == gid

    for i in items:
        _wait_terminal(client, token, i["id"])

    cmp_ = client.get(f"{API}/compare/{gid}", headers=_auth(token))
    assert cmp_.status_code == 200
    body = cmp_.json()
    assert body["conservative"]["execution_mode"] == "conservative"
    assert body["standard"]["status"] == "completed"
    assert body["annual_diff_percent"] is not None
    assert isinstance(body["sensitive"], bool)


def test_compare_unknown_group_404(client, token):
    assert client.get(f"{API}/compare/{uuid.uuid4()}",
                      headers=_auth(token)).status_code == 404


def test_rerun_creates_new_task(gw, client, token, strategy, monkeypatch):
    from app.api.v1.endpoints import backtests as bt_mod

    monkeypatch.setattr(bt_mod.settings, "BACKTEST_ENABLED", False)
    resp, _ = _create(gw, client, token, strategy, execution_mode="standard")
    tid = resp.json()[0]["id"]
    r = client.post(f"{API}/{tid}/rerun", headers=_auth(token))
    assert r.status_code == 201
    new = r.json()
    assert new["id"] != tid
    assert new["execution_mode"] == "standard"
    assert new["status"] == "pending"
