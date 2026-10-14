"""Tests for ``/api/v1/market/history/*`` (M2 历史数据管理).

The service instantiates ``GatewayClient`` directly (no FastAPI DI), so
``probe_history`` / ``history_backfill`` / ``rates`` are monkeypatched at
class level — same pattern as ``test_ticks_api.py``.  Covers: auth, probe
create/idempotent update/404-no-row, coverage listing, backfill tracking-404
/ span-422 / window-split call counts + finishing probe / gateway 503, sync
last_bar_time refresh + unavailable mapping, and untrack (delete + audit).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.services.gateway_client import (
    GatewayClient,
    GatewaySymbolNotFound,
    GatewayUnavailable,
)

API = "/api/v1/market/history"

# 2026-10-05 00:00:00 UTC — stable base epoch for all windows
T0 = 1_759_612_800

_probe_calls: list[tuple[str, str]] = []
_backfill_calls: list[tuple[str, str, int, int]] = []
_rates_calls: list[tuple[str, str, int]] = []
_fail_mode = ""  # "" | "unavailable" | "notfound"


def _sym() -> str:
    return f"TST{uuid.uuid4().hex[:6].upper()}"


@pytest.fixture()
def gw(monkeypatch: pytest.MonkeyPatch):
    """Deterministic gateway responses at class level."""
    _probe_calls.clear()
    _backfill_calls.clear()
    _rates_calls.clear()
    global _fail_mode
    _fail_mode = ""

    async def fake_probe_history(
        self: GatewayClient, name: str, timeframe: str, timeout: float | None = None
    ) -> dict:
        _probe_calls.append((name, timeframe))
        if _fail_mode == "notfound":
            raise GatewaySymbolNotFound(f"品种不存在：{name}")
        if _fail_mode == "unavailable":
            raise GatewayUnavailable("行情网关不可达：Connection refused")
        return {
            "symbol": name.upper(),
            "timeframe": timeframe,
            "first_bar_time": T0 - 3600,
            "last_bar_time": T0 + 7200,
            "bars_count": 40,
            "truncated": False,
            "max_bars": 1000,
        }

    async def fake_history_backfill(
        self: GatewayClient,
        name: str,
        timeframe: str,
        start: int,
        end: int,
        max_attempts: int | None = None,
        timeout: float | None = None,
    ) -> dict:
        _backfill_calls.append((name, timeframe, start, end))
        if _fail_mode == "unavailable":
            raise GatewayUnavailable("行情网关不可达：Connection refused")
        return {
            "first_bar_time": start,
            "last_bar_time": end - 60,
            "bars_count": (end - start) // 3600,
            "attempts": 2,
            "complete": True,
            "requested_start": start,
            "requested_end": end,
        }

    async def fake_rates(
        self: GatewayClient, name: str, timeframe: str, limit: int
    ) -> list[dict]:
        _rates_calls.append((name, timeframe, limit))
        if _fail_mode == "unavailable":
            raise GatewayUnavailable("行情网关不可达：Connection refused")
        if _fail_mode == "notfound":
            raise GatewaySymbolNotFound(f"品种不存在：{name}")
        return [{"time": T0 + 7200}, {"time": T0 + 10800}]

    monkeypatch.setattr(GatewayClient, "probe_history", fake_probe_history)
    monkeypatch.setattr(GatewayClient, "history_backfill", fake_history_backfill)
    monkeypatch.setattr(GatewayClient, "rates", fake_rates)
    return None


@pytest.fixture()
def token(client: TestClient) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"mhist-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"mh{uuid.uuid4().hex[:8]}",
            "password": "Str0ngPass!x",
            "full_name": "历史数据测试",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_history_requires_token(client: TestClient) -> None:
    assert client.get(f"{API}/coverage").status_code == 401
    assert client.post(f"{API}/probe", json={}).status_code == 401
    assert client.post(f"{API}/backfill", json={}).status_code == 401
    assert client.post(f"{API}/sync").status_code == 401
    assert client.delete(f"{API}/coverage/EURUSD/H1").status_code == 401


def test_probe_creates_and_updates(client: TestClient, token: str, gw) -> None:
    sym = _sym()
    r = client.post(
        f"{API}/probe",
        json={"symbol": sym, "timeframe": "H1"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == sym and body["timeframe"] == "H1"
    assert body["bars_count"] == 40
    assert datetime.fromisoformat(body["first_bar_time"]) == datetime.fromtimestamp(
        T0 - 3600, tz=UTC
    )
    assert body["probe_truncated"] is False and body["last_error"] is None

    # 幂等更新：同一对再探测仍是同一行（coverage 只有一条）
    r2 = client.post(
        f"{API}/probe",
        json={"symbol": sym.lower(), "timeframe": "H1"},  # 大小写归一
        headers=_auth(token),
    )
    assert r2.status_code == 200
    cov = client.get(f"{API}/coverage", headers=_auth(token)).json()
    rows = [i for i in cov["items"] if i["symbol"] == sym]
    assert len(rows) == 1


def test_probe_gateway_404_leaves_no_row(client: TestClient, token: str, gw) -> None:
    global _fail_mode
    _fail_mode = "notfound"
    sym = _sym()
    r = client.post(
        f"{API}/probe",
        json={"symbol": sym, "timeframe": "H1"},
        headers=_auth(token),
    )
    assert r.status_code == 404
    cov = client.get(f"{API}/coverage", headers=_auth(token)).json()
    assert all(i["symbol"] != sym for i in cov["items"])


def test_backfill_requires_tracking(client: TestClient, token: str, gw) -> None:
    r = client.post(
        f"{API}/backfill",
        json={"symbol": _sym(), "timeframe": "H1",
              "start": T0, "end": T0 + 3600},
        headers=_auth(token),
    )
    assert r.status_code == 404
    assert "探测" in r.json()["detail"]


def test_backfill_validation(client: TestClient, token: str, gw,
                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.config.settings.MARKET_HISTORY_BACKFILL_MAX_BARS", 10
    )
    sym = _sym()
    client.post(f"{API}/probe", json={"symbol": sym, "timeframe": "H1"},
                headers=_auth(token))

    # end <= start
    r = client.post(
        f"{API}/backfill",
        json={"symbol": sym, "timeframe": "H1", "start": T0, "end": T0},
        headers=_auth(token),
    )
    assert r.status_code == 422

    # 跨度折算根数超限（10 bars × 3600s 上限）
    orig = _backfill_calls[:]
    r = client.post(
        f"{API}/backfill",
        json={"symbol": sym, "timeframe": "H1",
              "start": T0, "end": T0 + 11 * 3600},
        headers=_auth(token),
    )
    assert r.status_code == 422
    assert _backfill_calls == orig  # 未发出任何网关调用


def test_backfill_window_split_and_probe(client: TestClient, token: str, gw,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    sym = _sym()
    client.post(f"{API}/probe", json={"symbol": sym, "timeframe": "H1"},
                headers=_auth(token))
    _probe_calls.clear()
    _backfill_calls.clear()
    monkeypatch.setattr(
        "app.config.settings.MARKET_HISTORY_BACKFILL_WINDOW_BARS", 10
    )

    start, end = T0, T0 + 3 * 10 * 3600  # 3 个窗口
    r = client.post(
        f"{API}/backfill",
        json={"symbol": sym, "timeframe": "H1", "start": start, "end": end},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["windows"] == 3
    assert body["attempts"] == 6  # 每窗 2 次
    assert body["complete"] is True
    assert body["requested_start"] == start and body["requested_end"] == end
    assert [(s, t, a, b) for s, t, a, b in _backfill_calls] == [
        (sym, "H1", start, start + 10 * 3600),
        (sym, "H1", start + 10 * 3600, start + 20 * 3600),
        (sym, "H1", start + 20 * 3600, end),
    ]
    # 收尾 probe 刷新快照：恰好补调一次
    assert _probe_calls == [(sym, "H1")]
    assert datetime.fromisoformat(body["first_bar_time"]) == datetime.fromtimestamp(
        T0 - 3600, tz=UTC
    )


def test_backfill_gateway_failure_503(client: TestClient, token: str, gw) -> None:
    global _fail_mode
    sym = _sym()
    client.post(f"{API}/probe", json={"symbol": sym, "timeframe": "H1"},
                headers=_auth(token))
    _fail_mode = "unavailable"
    r = client.post(
        f"{API}/backfill",
        json={"symbol": sym, "timeframe": "H1", "start": T0, "end": T0 + 3600},
        headers=_auth(token),
    )
    assert r.status_code == 503


def test_sync_updates_last_bar_time(client: TestClient, token: str, gw) -> None:
    sym = _sym()
    client.post(f"{API}/probe", json={"symbol": sym, "timeframe": "H1"},
                headers=_auth(token))
    r = client.post(f"{API}/sync", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] >= 1 and body["failed"] == 0

    cov = client.get(f"{API}/coverage", headers=_auth(token)).json()
    row = next(i for i in cov["items"] if i["symbol"] == sym)
    assert datetime.fromisoformat(row["last_bar_time"]) == datetime.fromtimestamp(
        T0 + 10800, tz=UTC
    )
    # first/bars_count 是 probe 快照，sync 不动
    assert datetime.fromisoformat(row["first_bar_time"]) == datetime.fromtimestamp(
        T0 - 3600, tz=UTC
    )
    assert row["bars_count"] == 40
    assert row["last_synced_at"] is not None
    assert row["last_error"] is None


def test_sync_gateway_unavailable(client: TestClient, token: str, gw) -> None:
    global _fail_mode
    sym = _sym()
    client.post(f"{API}/probe", json={"symbol": sym, "timeframe": "H1"},
                headers=_auth(token))
    _fail_mode = "unavailable"
    r = client.post(f"{API}/sync", headers=_auth(token))
    assert r.status_code == 200  # 不可达不炸端点，计 failed
    body = r.json()
    assert body["failed"] >= 1
    cov = client.get(f"{API}/coverage", headers=_auth(token)).json()
    # 不可达 → 首个请求对即 break 本轮；至少一行记录了错误
    assert any("不可达" in (i["last_error"] or "") for i in cov["items"])


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


def test_delete_coverage_and_audit(client: TestClient, token: str, gw) -> None:
    """删除追踪要留痕——留痕得查库，不能只查「有没有调用」。

    这个用例原来把 ``audit.record`` 替换成假函数、只断言调用发生：恰好把出问题
    的那一段换掉了。删追踪的审计行写在请求会话里没人 commit，请求结束就随会话
    一起回滚——调用当然发生过，库里却什么都没有。查库才看得见这件事。
    """
    sym = _sym()
    client.post(f"{API}/probe", json={"symbol": sym, "timeframe": "H1"},
                headers=_auth(token))
    r = client.delete(f"{API}/coverage/{sym}/H1", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"symbol": sym, "timeframe": "H1", "removed": True}
    assert _audit_actions(client, sym) == ["market_history.untrack"]

    cov = client.get(f"{API}/coverage", headers=_auth(token)).json()
    assert all(i["symbol"] != sym for i in cov["items"])

    # 重复删除 → 404（且不再留痕）
    r2 = client.delete(f"{API}/coverage/{sym}/H1", headers=_auth(token))
    assert r2.status_code == 404
    assert _audit_actions(client, sym) == ["market_history.untrack"]
