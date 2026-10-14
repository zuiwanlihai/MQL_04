"""Tests for ``/api/v1/ticks/*`` (M-Tick 数据管理) with a patched gateway.

The tick-sync worker runs inside the TestClient lifespan and instantiates
``GatewayClient`` directly (no FastAPI DI), so ``ticks_range`` is
monkeypatched at class level — the same pattern as the backtest queue tests.
Covers: auth requirement, sync-request validation (span/order/disabled),
worker execution (upsert dedup → coverage → list), gateway failure mapping
to job status=failed, purge with audit, and the request shape.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services.gateway_client import GatewayClient

API = "/api/v1/ticks"

# 2026-10-05 00:00:00 UTC — a stable window well inside the test data range
T0 = 1_759_612_800
TICKS = [
    {"time_msc": (T0 + 1) * 1000, "bid": 1.10000, "ask": 1.10012,
     "last": 0.0, "volume": 1, "flags": 30},
    {"time_msc": (T0 + 2) * 1000, "bid": 1.10002, "ask": 1.10014,
     "last": 0.0, "volume": 2, "flags": 30},
    {"time_msc": (T0 + 3) * 1000, "bid": 1.10004, "ask": 1.10016,
     "last": 0.0, "volume": 3, "flags": 30},
]

_calls: list[tuple[str, int, int, int]] = []
_fail_ticks = False


@pytest.fixture()
def gw(monkeypatch: pytest.MonkeyPatch):
    """Serve deterministic ticks straight from the GatewayClient class."""
    global _fail_ticks
    _calls.clear()
    _fail_ticks = False

    async def fake_ticks_range(
        self: GatewayClient, name: str, start: int, end: int, limit: int = 100_000
    ) -> list[dict]:
        _calls.append((name, start, end, limit))
        if _fail_ticks:
            from app.services.gateway_client import GatewayUnavailable

            raise GatewayUnavailable("行情网关不可达：Connection refused")
        return [dict(t) for t in TICKS]

    monkeypatch.setattr(GatewayClient, "ticks_range", fake_ticks_range)
    return _calls


@pytest.fixture()
def token(client: TestClient) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"ticks-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"tick{uuid.uuid4().hex[:8]}",
            "password": "Str0ngPass!x",
            "full_name": "Tick 测试",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _wait_terminal(
    client: TestClient, token: str, job_id: str, timeout: float = 15.0
) -> dict:
    """Poll the jobs endpoint until the worker reaches a terminal state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"{API}/jobs", headers=_auth(token))
        assert r.status_code == 200
        for item in r.json()["items"]:
            if item["id"] == job_id:
                if item["status"] in ("done", "failed"):
                    return item
                break
        time.sleep(0.1)
    raise AssertionError(f"tick job {job_id} did not reach a terminal state")


def test_ticks_requires_token(client: TestClient) -> None:
    assert client.post(f"{API}/sync", json={}).status_code == 401
    assert client.get(f"{API}/coverage").status_code == 401
    assert client.get(f"{API}/jobs").status_code == 401
    assert client.get(f"{API}/EURUSD").status_code == 401
    assert client.delete(f"{API}/EURUSD").status_code == 401


def test_sync_validation(client: TestClient, token: str) -> None:
    # end <= start
    r = client.post(
        f"{API}/sync", json={"symbol": "EURUSD", "start": T0, "end": T0},
        headers=_auth(token),
    )
    assert r.status_code == 422
    assert "结束时间必须晚于起始时间" in r.json()["detail"]
    # span > 7 days (default cap)
    r = client.post(
        f"{API}/sync",
        json={"symbol": "EURUSD", "start": T0, "end": T0 + 8 * 86_400},
        headers=_auth(token),
    )
    assert r.status_code == 422
    assert "不能超过 7 天" in r.json()["detail"]
    # extra key rejected
    r = client.post(
        f"{API}/sync",
        json={"symbol": "EURUSD", "start": T0, "end": T0 + 60, "nope": 1},
        headers=_auth(token),
    )
    assert r.status_code == 422


def test_sync_disabled_returns_503(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "TICK_SYNC_ENABLED", False)
    r = client.post(
        f"{API}/sync",
        json={"symbol": "EURUSD", "start": T0, "end": T0 + 60},
        headers=_auth(token),
    )
    assert r.status_code == 503
    assert "未启用" in r.json()["detail"]


def test_sync_executes_and_stores(
    client: TestClient, gw: list, token: str
) -> None:
    r = client.post(
        f"{API}/sync",
        json={"symbol": "eurusd", "start": T0, "end": T0 + 3600},
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "pending"
    assert job["symbol"] == "EURUSD"  # normalised to upper

    final = _wait_terminal(client, token, job["id"])
    assert final["status"] == "done"
    assert final["tick_count"] == len(TICKS)
    assert final["started_at"] and final["finished_at"]

    # chunked gateway calls: 1h window → single 6h slice
    assert gw == [("EURUSD", T0, T0 + 3600, 100_000)]

    # coverage aggregates (only this test's symbol)
    r = client.get(f"{API}/coverage", headers=_auth(token))
    assert r.status_code == 200
    cov = [c for c in r.json()["items"] if c["symbol"] == "EURUSD"]
    assert cov and cov[0]["tick_count"] == len(TICKS)
    assert cov[0]["min_time_msc"] == TICKS[0]["time_msc"]
    assert cov[0]["max_time_msc"] == TICKS[-1]["time_msc"]

    # re-running the same window is idempotent (conflict-do-nothing)
    r = client.post(
        f"{API}/sync",
        json={"symbol": "EURUSD", "start": T0, "end": T0 + 3600},
        headers=_auth(token),
    )
    final = _wait_terminal(client, token, r.json()["id"])
    assert final["status"] == "done" and final["tick_count"] == 0
    r = client.get(f"{API}/coverage", headers=_auth(token))
    cov = [c for c in r.json()["items"] if c["symbol"] == "EURUSD"]
    assert cov[0]["tick_count"] == len(TICKS)

    # stored rows readable ascending with an epoch-seconds filter
    r = client.get(
        f"{API}/EURUSD",
        params={"start": T0 + 2, "end": T0 + 3600},
        headers=_auth(token),
    )
    assert r.status_code == 200
    rows = r.json()["items"]
    assert [x["time_msc"] for x in rows] == [TICKS[1]["time_msc"], TICKS[2]["time_msc"]]
    assert rows[0]["bid"] == 1.10002


def test_sync_failure_maps_to_failed_status(
    client: TestClient, gw: list, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    global _fail_ticks
    _fail_ticks = True
    r = client.post(
        f"{API}/sync",
        json={"symbol": "XAUUSD", "start": T0, "end": T0 + 3600},
        headers=_auth(token),
    )
    assert r.status_code == 201
    job_id = r.json()["id"]

    final = _wait_terminal(client, token, job_id)
    assert final["status"] == "failed"
    assert "行情网关不可达" in final["error"]

    # no rows stored for that symbol
    r = client.get(f"{API}/coverage", headers=_auth(token))
    assert all(c["symbol"] != "XAUUSD" for c in r.json()["items"])


def test_purge_symbol(client: TestClient, gw: list, token: str) -> None:
    r = client.post(
        f"{API}/sync",
        json={"symbol": "EURUSD", "start": T0, "end": T0 + 3600},
        headers=_auth(token),
    )
    _wait_terminal(client, token, r.json()["id"])

    r = client.delete(f"{API}/EURUSD", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"symbol": "EURUSD", "deleted": len(TICKS)}

    r = client.get(f"{API}/coverage", headers=_auth(token))
    assert all(c["symbol"] != "EURUSD" for c in r.json()["items"])


def test_gateway_client_ticks_range_request_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GatewayClient.ticks_range hits /symbols/{name}/ticks/range."""
    from app.services.gateway_client import GatewaySymbolNotFound

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        if "NOSUCH" in request.url.path:
            return httpx.Response(404, json={"detail": "品种不存在：NOSUCH"})
        return httpx.Response(200, json=TICKS)

    c = GatewayClient(base_url="http://gw.test")
    c._client = httpx.AsyncClient(
        base_url="http://gw.test", transport=httpx.MockTransport(handler)
    )

    rows = asyncio.run(c.ticks_range("EURUSD", T0, T0 + 3600, limit=1000))
    assert rows == TICKS
    assert seen["path"] == "/symbols/EURUSD/ticks/range"
    assert seen["params"] == {
        "start": str(T0), "end": str(T0 + 3600), "limit": "1000",
    }

    with pytest.raises(GatewaySymbolNotFound):
        asyncio.run(c.ticks_range("NOSUCH", T0, T0 + 3600))
