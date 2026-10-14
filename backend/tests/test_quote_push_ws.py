"""WS quote-push tests (行情页实时化 MQ-1).

Covers: ticket issuance/auth/single-use/expiry, WS subscribe/unsubscribe
acks, lazy pump (no subscribers → no gateway call), change detection,
per-connection symbol limit, gateway-error degradation frame, ping frame,
and disconnect cleanup.  The background pump is frozen via a huge poll
interval; tests drive ``hub.pump_once()`` deterministically through the
TestClient portal (the app's own event loop).
"""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.services.gateway_client import GatewayError
from app.services.market_mapper import map_quote
from app.services.quote_push import TICKET_TTL_SECONDS, get_quote_hub

API = "/api/v1/market"
WS_URL = "/ws/quotes"

ROWS = [
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
    {
        "symbol": "GBPUSD",
        "description": "Sterling vs US Dollar",
        "bid": 1.26500,
        "ask": 1.26515,
        "last": 1.26505,
        "prev_close": 1.26000,
        "time": 1760000000,
        "point": 0.00001,
    },
]


class CountingGateway:
    """Minimal stand-in for the hub's GatewayClient (quotes only)."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.calls: list[list[str]] = []
        self.fail = False

    async def quotes(self, names: list[str]) -> list[dict]:
        self.calls.append(list(names))
        if self.fail:
            raise GatewayError("行情网关不可达：boom")
        wanted = set(names)
        return [r for r in self.rows if r["symbol"] in wanted]

    async def aclose(self) -> None:  # pragma: no cover - compatibility
        pass


@pytest.fixture()
def token(client: TestClient) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"ws-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"ws{uuid.uuid4().hex[:8]}",
            "password": "Str0ngPass!x",
            "full_name": "WS推送测试",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


@pytest.fixture()
def hub(client: TestClient):
    h = get_quote_hub()
    assert h is not None, "lifespan 应已创建 QuoteHub（MARKET_PUSH_ENABLED 默认开）"
    h._snapshot.clear()
    h._tickets.clear()
    h.error_backoff = 1.0
    return h


@pytest.fixture()
def fake(hub, monkeypatch):
    """Swap in a counting gateway and freeze the background pump loop."""
    g = CountingGateway()
    monkeypatch.setattr(hub, "gateway", g)
    monkeypatch.setattr(
        "app.config.settings.MARKET_PUSH_POLL_SECONDS", 999.0
    )
    return g


def _ticket(client: TestClient, token: str) -> str:
    r = client.post(f"{API}/ws-ticket", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


def _expect_disconnect(ws) -> int:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        ws.receive_json()
    return excinfo.value.code


# ── REST ticket endpoint ─────────────────────────────────────────────────────

def test_ws_ticket_requires_auth(client: TestClient) -> None:
    assert client.post(f"{API}/ws-ticket").status_code == 401


def test_ws_ticket_issued(client: TestClient, token: str) -> None:
    r = client.post(f"{API}/ws-ticket", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["ticket"]
    assert body["expires_in"] == TICKET_TTL_SECONDS


# ── WS auth ──────────────────────────────────────────────────────────────────

def test_ws_rejects_missing_ticket(client: TestClient) -> None:
    with client.websocket_connect(WS_URL) as ws:
        assert _expect_disconnect(ws) == 4401


def test_ws_rejects_bad_ticket(client: TestClient) -> None:
    with client.websocket_connect(f"{WS_URL}?ticket=bogus") as ws:
        assert _expect_disconnect(ws) == 4401


def test_ws_ticket_single_use(client: TestClient, token: str) -> None:
    ticket = _ticket(client, token)
    with client.websocket_connect(f"{WS_URL}?ticket={ticket}") as ws:
        ws.send_json({"action": "subscribe", "symbols": ["EURUSD"]})
        assert ws.receive_json()["type"] == "subscribed"
    with client.websocket_connect(f"{WS_URL}?ticket={ticket}") as ws:
        assert _expect_disconnect(ws) == 4401


def test_ws_ticket_expiry(client: TestClient, token: str, hub) -> None:
    ticket = _ticket(client, token)
    hub._tickets[ticket] = time.time() - 1  # force-expire
    with client.websocket_connect(f"{WS_URL}?ticket={ticket}") as ws:
        assert _expect_disconnect(ws) == 4401


# ── Pump / broadcast ─────────────────────────────────────────────────────────

def test_pump_lazy_without_subscribers(client: TestClient, hub, fake) -> None:
    client.portal.call(hub.pump_once)
    assert fake.calls == []


def test_subscribe_ack_then_broadcast_on_change(
    client: TestClient, token: str, hub, fake
) -> None:
    fake.rows = ROWS
    ticket = _ticket(client, token)
    with client.websocket_connect(f"{WS_URL}?ticket={ticket}") as ws:
        ws.send_json({"action": "subscribe", "symbols": ["eurusd"]})
        ack = ws.receive_json()
        assert ack == {"type": "subscribed", "symbols": ["EURUSD"]}

        # 首轮：新快照视为变化 → 广播
        client.portal.call(hub.pump_once)
        frame = ws.receive_json()
        assert frame["type"] == "quotes"
        assert [i["symbol_id"] for i in frame["items"]] == ["EURUSD"]
        assert frame["items"][0]["bid"] == 1.085

        # 无变化 → 不推帧（通过网关未被再次调用来证明泵确实跑过但静默）
        calls_before = len(fake.calls)
        client.portal.call(hub.pump_once)
        assert len(fake.calls) == calls_before + 1

        # 行情变化 → 推新值
        fake.rows[0] = {**fake.rows[0], "bid": 1.08600, "ask": 1.08612}
        client.portal.call(hub.pump_once)
        frame = ws.receive_json()
        assert frame["type"] == "quotes"
        assert frame["items"][0]["bid"] == 1.086


def test_detect_changed_fingerprint(hub) -> None:
    dump = map_quote(ROWS[0]).model_dump(mode="json")
    assert hub.detect_changed([dump]) == [dump]  # 首见 → 变化
    assert hub.detect_changed([dump]) == []  # 快照一致 → 静默
    moved = {**dump, "bid": dump["bid"] + 0.0001}
    assert hub.detect_changed([moved]) == [moved]


def test_map_quote_bar_time_axis() -> None:
    """bar_time 与 K 线（UTC 伪 epoch）同轴；缺失时为 None 由前端回退。"""
    row = {**ROWS[0], "bar_time": ROWS[0]["time"] - 10800}
    out = map_quote(row)
    assert out.bar_time is not None
    assert out.bar_time.endswith("+00:00")
    assert out.time != out.bar_time  # tick 轴（服务器时区）≠ bar 轴
    # 缺失 bar_time（旧网关）→ None，前端回退 time
    legacy = map_quote(ROWS[0])
    assert legacy.bar_time is None


def test_unsubscribe_stops_poll_and_delivery(
    client: TestClient, token: str, hub, fake
) -> None:
    fake.rows = ROWS
    ticket = _ticket(client, token)
    with client.websocket_connect(f"{WS_URL}?ticket={ticket}") as ws:
        ws.send_json({"action": "subscribe", "symbols": ["EURUSD", "GBPUSD"]})
        assert ws.receive_json()["type"] == "subscribed"

        ws.send_json({"action": "unsubscribe", "symbols": ["GBPUSD"]})
        ack = ws.receive_json()
        assert ack["type"] == "unsubscribed"
        assert hub.subscribed_symbols() == ["EURUSD"]

        ws.send_json({"action": "unsubscribe", "symbols": ["EURUSD"]})
        assert ws.receive_json()["type"] == "unsubscribed"
        assert hub.subscribed_symbols() == []

        # 无订阅者 → 泵空转，不再请求网关
        calls_before = len(fake.calls)
        client.portal.call(hub.pump_once)
        assert len(fake.calls) == calls_before


def test_disconnect_cleans_registry(client: TestClient, token: str, hub) -> None:
    ticket = _ticket(client, token)
    with client.websocket_connect(f"{WS_URL}?ticket={ticket}") as ws:
        ws.send_json({"action": "subscribe", "symbols": ["EURUSD"]})
        assert ws.receive_json()["type"] == "subscribed"
        assert hub.subscribed_symbols() == ["EURUSD"]
    assert hub.subscribed_symbols() == []


def test_symbol_limit_rejected_whole_frame(
    client: TestClient, token: str, hub
) -> None:
    ticket = _ticket(client, token)
    with client.websocket_connect(f"{WS_URL}?ticket={ticket}") as ws:
        ws.send_json(
            {"action": "subscribe", "symbols": [f"S{i:03d}" for i in range(51)]}
        )
        err = ws.receive_json()
        assert err["type"] == "error"
        assert "超限" in err["detail"]
        # 整帧拒绝：没有部分注册
        assert hub.subscribed_symbols() == []
        # 超限后连接仍可用
        ws.send_json({"action": "subscribe", "symbols": ["EURUSD"]})
        assert ws.receive_json()["symbols"] == ["EURUSD"]


def test_unknown_action_error_frame(client: TestClient, token: str) -> None:
    ticket = _ticket(client, token)
    with client.websocket_connect(f"{WS_URL}?ticket={ticket}") as ws:
        ws.send_json({"action": "bogus"})
        err = ws.receive_json()
        assert err["type"] == "error"
        assert "未知 action" in err["detail"]


def test_pump_gateway_error_frame_and_recovery(
    client: TestClient, token: str, hub, fake
) -> None:
    fake.rows = ROWS
    fake.fail = True
    ticket = _ticket(client, token)
    with client.websocket_connect(f"{WS_URL}?ticket={ticket}") as ws:
        ws.send_json({"action": "subscribe", "symbols": ["EURUSD"]})
        assert ws.receive_json()["type"] == "subscribed"

        client.portal.call(hub.pump_once)
        err = ws.receive_json()
        assert err["type"] == "gateway_error"
        assert "boom" in err["detail"]
        assert hub.error_backoff == pytest.approx(2.0)  # 1s → 2s

        # 恢复：连接保留，继续推行情，退避复位
        fake.fail = False
        client.portal.call(hub.pump_once)
        frame = ws.receive_json()
        assert frame["type"] == "quotes"
        assert hub.error_backoff == pytest.approx(1.0)


def test_ping_frame(client: TestClient, token: str, hub) -> None:
    ticket = _ticket(client, token)
    with client.websocket_connect(f"{WS_URL}?ticket={ticket}") as ws:
        ws.send_json({"action": "subscribe", "symbols": ["EURUSD"]})
        assert ws.receive_json()["type"] == "subscribed"
        client.portal.call(hub.ping_all)
        assert ws.receive_json() == {"type": "ping"}
