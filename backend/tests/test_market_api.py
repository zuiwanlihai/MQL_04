"""Tests for ``/api/v1/market/*`` with a stubbed gateway client.

Covers: auth requirement, group/search filtering, symbol 404, kline
validation + ascending order, quote derivation, and the unified 503
Chinese message when the gateway is unreachable.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

API = "/api/v1/market"

SYMBOLS = [
    {
        "name": "EURUSD",
        "path": "Forex\\Major",
        "description": "Euro vs US Dollar",
        "digits": 5,
        "point": 0.00001,
        "spread": 12,
        "trade_contract_size": 100000,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "swap_long": -3.5,
        "swap_short": 1.2,
        "margin_initial": 100,
        "trade_mode": 0,
        "session_open": 100,
        "session_close": 2359,
        "currency_base": "EUR",
        "currency_profit": "USD",
        "trade_tick_size": 0.00001,
        "trade_tick_value": 1.0,
        "visible": True,
    },
    {
        "name": "XAUUSD",
        "path": "Metals\\Spot",
        "description": "Gold vs US Dollar",
        "digits": 2,
        "point": 0.01,
        "spread": 25,
        "trade_mode": 0,
        "session_open": None,
        "session_close": None,
        "visible": True,
    },
    {
        "name": "US500",
        "path": "Indices\\US",
        "description": "S&P 500 Index",
        "digits": 2,
        "point": 0.01,
        "spread": 5,
        "trade_mode": 1,  # not tradeable
        "visible": False,
    },
]

QUOTES = [
    {
        "symbol": "EURUSD",
        "description": "Euro vs US Dollar",
        "bid": 1.08500,
        "ask": 1.08512,
        "last": 1.08505,
        "prev_close": 1.08000,
        "time": 1760000000,
        "high_day": 1.08600,
        "low_day": 1.07900,
        "volume": 12345,
        "point": 0.00001,
    },
]

RATES = [
    {"time": 1760007200, "open": 2, "high": 3, "low": 1, "close": 2.5,
     "tick_volume": 10, "spread": 2, "real_volume": 0},
    {"time": 1760003600, "open": 1, "high": 2, "low": 0.5, "close": 2,
     "tick_volume": 20, "spread": 1, "real_volume": 0},
]


class StubGateway:
    """Stand-in for GatewayClient (dependency override)."""

    async def symbols(self) -> list[dict]:
        self._maybe_fail()
        return SYMBOLS

    async def rates(self, name: str, timeframe: str, limit: int) -> list[dict]:
        self._maybe_fail()
        if name not in {s["name"] for s in SYMBOLS}:
            from app.services.gateway_client import GatewaySymbolNotFound

            raise GatewaySymbolNotFound(f"品种不存在：{name}")
        return RATES

    async def quotes(self, names: list[str]) -> list[dict]:
        self._maybe_fail()
        return [q for q in QUOTES if q["symbol"] in names]

    async def aclose(self) -> None:  # pragma: no cover - compatibility
        pass

    @staticmethod
    def _maybe_fail() -> None:
        from app.services.gateway_client import GatewayUnavailable

        if _stub_state.unavailable:
            raise GatewayUnavailable(
                "行情网关不可达：Connection refused。"
                "请在「行情 · 数据管理」页确认网关服务已启动"
            )


class _State:
    unavailable = False


_stub_state = _State()


@pytest.fixture()
def stub() -> StubGateway:
    from app.main import app
    from app.services.gateway_client import get_gateway_client

    stub = StubGateway()
    _stub_state.unavailable = False
    app.dependency_overrides[get_gateway_client] = lambda: stub
    yield stub
    app.dependency_overrides.pop(get_gateway_client, None)


@pytest.fixture()
def token(client: TestClient) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"market-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"mkt{uuid.uuid4().hex[:8]}",
            "password": "Str0ngPass!x",
            "full_name": "行情测试",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_market_requires_token(client: TestClient, stub: StubGateway) -> None:
    assert client.get(f"{API}/symbols").status_code == 401
    assert client.get(f"{API}/quotes", params={"symbols": "EURUSD"}).status_code == 401


def test_symbols_list_and_filters(
    client: TestClient, stub: StubGateway, token: str
) -> None:
    r = client.get(f"{API}/symbols", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    assert body["items"][0]["id"] == "EURUSD"
    # 映射校验
    eurusd = body["items"][0]
    assert eurusd["group"] == "forex"
    assert eurusd["base_currency"] == "EUR"
    assert eurusd["session_open"] == "01:00"
    assert eurusd["session_close"] == "23:59"
    assert eurusd["is_trade_allowed"] is True
    gold = next(i for i in body["items"] if i["id"] == "XAUUSD")
    assert gold["group"] == "commodities"
    assert gold["session_open"] == ""
    spx = next(i for i in body["items"] if i["id"] == "US500")
    assert spx["group"] == "indices"
    assert spx["is_trade_allowed"] is False
    assert spx["is_active"] is False

    # group 过滤
    r = client.get(f"{API}/symbols", params={"group": "forex"}, headers=_auth(token))
    assert r.status_code == 200
    assert [i["name"] for i in r.json()["items"]] == ["EURUSD"]

    # 搜索（大小写不敏感，命中名称或描述）
    r = client.get(f"{API}/symbols", params={"q": "gold"}, headers=_auth(token))
    assert r.status_code == 200
    assert [i["name"] for i in r.json()["items"]] == ["XAUUSD"]


def test_symbol_detail_and_404(
    client: TestClient, stub: StubGateway, token: str
) -> None:
    r = client.get(f"{API}/symbols/EURUSD", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["description"] == "Euro vs US Dollar"

    r = client.get(f"{API}/symbols/NOSUCH", headers=_auth(token))
    assert r.status_code == 404
    assert r.json()["detail"] == "品种不存在：NOSUCH"


def test_klines_sorted_and_validated(
    client: TestClient, stub: StubGateway, token: str
) -> None:
    r = client.get(
        f"{API}/symbols/EURUSD/klines",
        params={"timeframe": "H1", "limit": 100},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    bars = r.json()
    assert [b["time"] for b in bars] == sorted(b["time"] for b in bars)
    assert bars[0]["open"] == 1 and bars[1]["close"] == 2.5

    # 非法周期 / 越界 limit → 422
    r = client.get(
        f"{API}/symbols/EURUSD/klines", params={"timeframe": "Y1"}, headers=_auth(token)
    )
    assert r.status_code == 422
    r = client.get(
        f"{API}/symbols/EURUSD/klines", params={"limit": 0}, headers=_auth(token)
    )
    assert r.status_code == 422
    r = client.get(
        f"{API}/symbols/EURUSD/klines", params={"limit": 5001}, headers=_auth(token)
    )
    assert r.status_code == 422


def test_quotes_derivation(client: TestClient, stub: StubGateway, token: str) -> None:
    r = client.get(f"{API}/quotes", params={"symbols": "EURUSD"}, headers=_auth(token))
    assert r.status_code == 200, r.text
    q = r.json()["items"][0]
    assert q["symbol_id"] == "EURUSD"
    assert q["spread"] == 12  # (ask-bid)/point
    assert round(q["change"], 5) == 0.00505  # last - prev_close
    assert q["change_percent"] > 0
    assert q["high_day"] == 1.086
    assert q["time"]  # ISO string

    # 数量越界 → 422 中文
    many = ",".join(["EURUSD"] * 51)
    r = client.get(f"{API}/quotes", params={"symbols": many}, headers=_auth(token))
    assert r.status_code == 422
    assert r.json()["detail"] == "symbols 数量须在 1–50 之间"


def test_gateway_client_rates_range_request_shape() -> None:
    """GatewayClient.rates_range hits /symbols/{name}/rates/range with params."""
    import httpx

    from app.services.gateway_client import (
        GatewayClient,
        GatewaySymbolNotFound,
    )

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        if "NOSUCH" in request.url.path:
            return httpx.Response(404, json={"detail": "品种不存在：NOSUCH"})
        return httpx.Response(200, json=RATES)

    client_ = GatewayClient(base_url="http://gw.test")
    client_._client = httpx.AsyncClient(
        base_url="http://gw.test", transport=httpx.MockTransport(handler)
    )

    import asyncio

    rows = asyncio.run(client_.rates_range("EURUSD", "H1", 100, 200, limit=5000))
    assert rows == RATES
    assert seen["path"] == "/symbols/EURUSD/rates/range"
    assert seen["params"] == {
        "timeframe": "H1",
        "start": "100",
        "end": "200",
        "limit": "5000",
    }

    with pytest.raises(GatewaySymbolNotFound):
        asyncio.run(client_.rates_range("NOSUCH", "H1", 100, 200))


def test_gateway_unavailable_returns_503_chinese(
    client: TestClient, stub: StubGateway, token: str
) -> None:
    _stub_state.unavailable = True
    try:
        r = client.get(f"{API}/symbols", headers=_auth(token))
        assert r.status_code == 503
        detail = r.json()["detail"]
        assert "行情网关不可达" in detail
        assert "行情 · 数据管理" in detail
    finally:
        _stub_state.unavailable = False
