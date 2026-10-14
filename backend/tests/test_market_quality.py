"""Data quality check API tests (数据中心 P1: 缺口/异常值).

The gateway surface is stubbed via the ``get_gateway_client`` dependency
override (same pattern as ``test_market_api.py``); synthetic bar windows
exercise each detector deterministically.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

API = "/api/v1/market/quality"

# Monday 2026-03-02 00:00 UTC — deterministic week anchor.
MON0 = int(datetime(2026, 3, 2, tzinfo=UTC).timestamp())
H1 = 3600


def bar(t: int, *, o=1.0, h=1.01, low=0.99, c=1.0, vol=100) -> dict:
    return {"time": t, "open": o, "high": h, "low": low, "close": c, "tick_volume": vol}


def clean_window(n: int, start: int = MON0) -> list[dict]:
    return [bar(start + i * H1) for i in range(n)]


class QualityStub:
    """Serves a canned rates_range page; toggled per test via ``bars``."""

    def __init__(self) -> None:
        self.bars: list[dict] = []
        self.unavailable = False

    async def rates_range(self, name, timeframe, start, end, limit=5000):
        from app.services.gateway_client import GatewayUnavailable

        if self.unavailable:
            raise GatewayUnavailable(
                "行情网关不可达：Connection refused。"
                "请在「行情 · 数据管理」页确认网关服务已启动"
            )
        return [b for b in self.bars if start <= b["time"] < end][:limit]

    async def aclose(self) -> None:  # pragma: no cover
        pass


@pytest.fixture()
def stub() -> QualityStub:
    from app.main import app
    from app.services.gateway_client import get_gateway_client

    stub = QualityStub()
    app.dependency_overrides[get_gateway_client] = lambda: stub
    yield stub
    app.dependency_overrides.pop(get_gateway_client, None)


@pytest.fixture()
def token(client: TestClient) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"quality-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"qual{uuid.uuid4().hex[:8]}",
            "password": "Str0ngPass!x",
            "full_name": "质量检查测试",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _get(client: TestClient, token: str, **params):
    base = {"symbol": "EURUSD", "timeframe": "H1", "start": MON0, "end": MON0 + 24 * H1}
    base.update(params)
    return client.get(API, params=base, headers=_auth(token))


def test_quality_requires_token(client: TestClient, stub: QualityStub) -> None:
    assert client.get(API, params={"symbol": "EURUSD"}).status_code == 401


def test_clean_window_scores_full(client: TestClient, stub: QualityStub, token: str) -> None:
    stub.bars = clean_window(24)
    r = _get(client, token)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bars_analyzed"] == 24
    assert body["quality_score"] == 100
    assert body["verdict"] == "ok"
    assert body["gap_count"] == 0 and body["invalid_count"] == 0


def test_gap_detection(client: TestClient, stub: QualityStub, token: str) -> None:
    # Weekday gap: drop 2 bars mid-Monday (hours 5,6) → 1 error gap.
    weekday = [b for b in clean_window(24) if (b["time"] - MON0) // H1 not in (5, 6)]
    stub.bars = weekday
    r = _get(client, token)
    assert r.status_code == 200
    body = r.json()
    assert body["gap_count"] == 1
    assert body["weekend_gap_count"] == 0
    assert body["quality_score"] < 100
    gap = next(i for i in body["issues"] if i["code"] == "gap")
    assert gap["severity"] == "error"
    assert "缺失约 2 根" in gap["message"]


def test_weekend_gap_is_informational(
    client: TestClient, stub: QualityStub, token: str
) -> None:
    # Full Mon–Fri week + one Sunday bar: the Fri→Sun closure is normal FX
    # schedule and must not count as an error gap.
    stub.bars = clean_window(5 * 24) + [bar(MON0 + 6 * 86400 + 23 * H1)]
    r = _get(client, token, end=MON0 + 7 * 86400)
    assert r.status_code == 200
    body = r.json()
    assert body["gap_count"] == 0
    assert body["weekend_gap_count"] == 1
    issue = next(i for i in body["issues"] if i["code"] == "weekend_gap")
    assert issue["severity"] == "info"
    assert body["quality_score"] == 100


def test_invalid_ohlc_detected(client: TestClient, stub: QualityStub, token: str) -> None:
    bars = clean_window(6)
    broken = bar(MON0 + 3 * H1, o=1.0, h=0.9, low=0.99, c=1.05)  # high < close
    bars.insert(3, broken)
    stub.bars = bars
    r = _get(client, token)
    body = r.json()
    assert body["invalid_count"] == 1
    issue = next(i for i in body["issues"] if i["code"] == "invalid_ohlc")
    assert issue["severity"] == "error"
    assert issue["start_time"] == MON0 + 3 * H1


def test_price_spike_warns(client: TestClient, stub: QualityStub, token: str) -> None:
    bars = clean_window(6)
    bars[4] = bar(MON0 + 4 * H1, o=1.0, h=1.51, low=0.99, c=1.5)  # +50% jump
    bars[5] = bar(MON0 + 5 * H1, o=1.5, h=1.51, low=1.49, c=1.5)  # stay high
    stub.bars = bars
    r = _get(client, token)
    body = r.json()
    assert body["spike_count"] == 1
    issue = next(i for i in body["issues"] if i["code"] == "price_spike")
    assert issue["severity"] == "warning"
    # 0.2-point penalty keeps the verdict on the OK side.
    assert body["verdict"] == "ok"


def test_duplicate_and_zero_volume(
    client: TestClient, stub: QualityStub, token: str
) -> None:
    bars = clean_window(5)
    bars.append(bar(MON0 + 2 * H1))  # duplicate timestamp
    bars[1] = bar(MON0 + H1, vol=0)  # zero tick volume
    stub.bars = bars
    r = _get(client, token)
    body = r.json()
    assert body["duplicate_count"] == 1
    assert body["zero_volume_count"] == 1
    codes = {i["code"] for i in body["issues"]}
    assert "duplicate_time" in codes and "zero_volume" in codes


def test_poor_verdict_on_heavy_damage(
    client: TestClient, stub: QualityStub, token: str
) -> None:
    bars = []
    for i in range(24):
        if i in (2, 3, 8, 9, 14, 15, 20, 21):
            continue  # 4 gaps → −8
        b = bar(MON0 + i * H1)
        if i in (5, 10):
            b = bar(MON0 + i * H1, o=1.0, h=0.5, low=0.99, c=1.0)  # invalid OHLC
        bars.append(b)
    stub.bars = bars
    r = _get(client, token)
    body = r.json()
    assert body["gap_count"] == 4
    assert body["invalid_count"] == 2
    assert body["verdict"] == "warn"


def test_start_end_validation(client: TestClient, stub: QualityStub, token: str) -> None:
    r = _get(client, token, start=MON0 + H1, end=MON0)
    assert r.status_code == 422
    assert r.json()["detail"] == "start 必须早于 end"


def test_gateway_unavailable_503(
    client: TestClient, stub: QualityStub, token: str
) -> None:
    stub.unavailable = True
    r = _get(client, token)
    assert r.status_code == 503
    assert "行情网关不可达" in r.json()["detail"]
