"""Tests for ``/api/v1/notebooks`` (M18 研究笔记本) + queue overview.

CRUD / sanitize / ownership tests go through the HTTP layer.  Run tests
execute a **real** sandboxed subprocess (``app.sandbox.notebook_runner``)
via the shared RunnerSupervisor — the same proven path as backtest queue
tests.  Gateway bar pre-fetch is stubbed at class level (the kernel
instantiates ``GatewayClient`` directly, no DI).
"""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services import notebook_kernel
from app.services.gateway_client import GatewayClient

API = "/api/v1/notebooks"

_BASE_TS = 1_759_612_800  # 2026-10-05 00:00:00 UTC


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, tag: str) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"nb-{tag}-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"nb{tag}{uuid.uuid4().hex[:8]}",
            "password": "Str0ngPass!x",
            "full_name": "Notebook 测试",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


@pytest.fixture()
def token(client: TestClient) -> str:
    return _register(client, "a")


@pytest.fixture()
def token2(client: TestClient) -> str:
    return _register(client, "b")


@pytest.fixture()
def gw(monkeypatch: pytest.MonkeyPatch):
    """Deterministic OHLCV rows straight from the GatewayClient class."""
    calls: list[tuple[str, str, int]] = []

    async def fake_rates(
        self: GatewayClient, symbol: str, timeframe: str, count: int
    ) -> list[dict]:
        calls.append((symbol, timeframe, count))
        return [
            {
                "time": _BASE_TS + i * 3600,
                "open": 1.10 + i * 0.0001,
                "high": 1.11 + i * 0.0001,
                "low": 1.09 + i * 0.0001,
                "close": 1.105 + i * 0.0001,
                "tick_volume": 100 + i,
                "spread": 12,
                "real_volume": 0,
            }
            for i in range(count)
        ]

    monkeypatch.setattr(GatewayClient, "rates", fake_rates)
    return calls


def _create(client: TestClient, token: str, title: str = "研究笔记本 A") -> dict:
    r = client.post(API, json={"title": title}, headers=_auth(token))
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# CRUD / ownership
# ---------------------------------------------------------------------------


def test_notebook_crud_flow(client: TestClient, token: str):
    nb = _create(client, token)
    assert nb["title"] == "研究笔记本 A"
    assert nb["cell_count"] == 1
    assert nb["cells"][0]["type"] == "code"
    assert nb["cells"][0]["outputs"] == []

    # list
    r = client.get(API, headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert any(item["id"] == nb["id"] for item in body["items"])
    assert "cells" not in body["items"][0]

    # patch title + cells
    cells = [
        {"id": "c1", "type": "code", "source": "x = 1", "outputs": []},
        {"id": "c2", "type": "markdown", "source": "# 标题", "outputs": []},
    ]
    r = client.patch(
        f"{API}/{nb['id']}", json={"title": "改名", "cells": cells}, headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "改名"
    assert r.json()["cell_count"] == 2

    # invalid cell type rejected by schema
    r = client.patch(
        f"{API}/{nb['id']}",
        json={"cells": [{"id": "c", "type": "shell", "source": "", "outputs": []}]},
        headers=_auth(token),
    )
    assert r.status_code == 422

    # delete → 404 afterwards
    r = client.delete(f"{API}/{nb['id']}", headers=_auth(token))
    assert r.status_code == 204
    r = client.get(f"{API}/{nb['id']}", headers=_auth(token))
    assert r.status_code == 404


def test_notebook_cross_user_404(client: TestClient, token: str, token2: str):
    nb = _create(client, token)
    for method, payload in (
        ("get", None),
        ("patch", {"title": "hijack"}),
        ("delete", None),
        ("post", None),  # run with body added separately below
    ):
        url = f"{API}/{nb['id']}"
        if method == "post":
            r = client.post(
                f"{url}/run", json={"cell_id": "c1", "source": "1"}, headers=_auth(token2)
            )
        elif method == "patch":
            r = client.patch(url, json=payload, headers=_auth(token2))
        else:
            r = getattr(client, method)(url, headers=_auth(token2))
        assert r.status_code == 404, (method, r.text)
    notebook_kernel.reset_session(uuid.UUID(nb["id"]))


def test_notebook_sanitize_guards(client: TestClient, token: str):
    nb = _create(client, token)

    # too many cells
    flood = [{"id": f"c{i}", "type": "code", "source": "1", "outputs": []} for i in range(101)]
    r = client.patch(f"{API}/{nb['id']}", json={"cells": flood}, headers=_auth(token))
    assert r.status_code == 422

    # single oversize cell
    r = client.patch(
        f"{API}/{nb['id']}",
        json={"cells": [{"id": "c", "type": "code", "source": "a" * 200_001, "outputs": []}]},
        headers=_auth(token),
    )
    assert r.status_code == 422

    # oversized persisted output → truncated with a note
    big_out = [{"kind": "stdout", "text": "x" * 70_000}]
    r = client.patch(
        f"{API}/{nb['id']}",
        json={"cells": [{"id": "c", "type": "code", "source": "1", "outputs": big_out}]},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    outs = r.json()["cells"][0]["outputs"]
    assert len(str(outs)) < 70_000
    assert any("截断" in str(item) for item in outs)


# ---------------------------------------------------------------------------
# Cell execution (real subprocess)
# ---------------------------------------------------------------------------


def test_run_simple_cell_and_session_state(client: TestClient, token: str):
    nb = _create(client, token)
    nb_id = nb["id"]

    r = client.post(
        f"{API}/{nb_id}/run",
        json={"cell_id": "cell-1", "source": "a = 41\nprint('hello')\na + 1"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["result_repr"] == "42"
    assert body["stdout"] == "hello\n"
    assert body["state_reset"] is False

    # state persists across cells
    r = client.post(
        f"{API}/{nb_id}/run",
        json={"cell_id": "cell-1", "source": "a * 2"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["result_repr"] == "82"
    notebook_kernel.reset_session(uuid.UUID(nb_id))


def test_run_exception_keeps_state(client: TestClient, token: str):
    nb = _create(client, token)
    nb_id = nb["id"]

    r = client.post(
        f"{API}/{nb_id}/run",
        json={"cell_id": "cell-1", "source": "k = 7\n1 / 0"},
        headers=_auth(token),
    )
    body = r.json()
    assert body["ok"] is False
    assert body["etype"] == "ZeroDivisionError"
    assert body["state_reset"] is False

    # a finished-with-error cell still saved its globals
    r = client.post(
        f"{API}/{nb_id}/run",
        json={"cell_id": "cell-1", "source": "k + 1"},
        headers=_auth(token),
    )
    body = r.json()
    assert body["ok"] is True
    assert body["result_repr"] == "8"
    notebook_kernel.reset_session(uuid.UUID(nb_id))


def test_run_import_blocked(client: TestClient, token: str):
    nb = _create(client, token)
    r = client.post(
        f"{API}/{nb['id']}/run",
        json={"cell_id": "cell-1", "source": "import os"},
        headers=_auth(token),
    )
    body = r.json()
    assert body["ok"] is False
    assert body["etype"] == "SandboxViolationError"
    assert "白名单" in (body["message"] or "")
    assert body["terminated"] == "failed"
    assert body["state_reset"] is True
    notebook_kernel.reset_session(uuid.UUID(nb["id"]))


def test_run_dangerous_builtin_removed(client: TestClient, token: str):
    nb = _create(client, token)
    r = client.post(
        f"{API}/{nb['id']}/run",
        json={"cell_id": "cell-1", "source": "open('x.txt', 'w')"},
        headers=_auth(token),
    )
    body = r.json()
    assert body["ok"] is False
    assert body["etype"] == "NameError"
    assert body["state_reset"] is False
    notebook_kernel.reset_session(uuid.UUID(nb["id"]))


def test_run_syntax_error(client: TestClient, token: str):
    nb = _create(client, token)
    r = client.post(
        f"{API}/{nb['id']}/run",
        json={"cell_id": "cell-1", "source": "def broken(:"},
        headers=_auth(token),
    )
    body = r.json()
    assert body["ok"] is False
    assert body["etype"] == "SyntaxError"
    assert body["terminated"] == "failed"
    notebook_kernel.reset_session(uuid.UUID(nb["id"]))


def test_run_get_bars_prefetch_and_guiding_error(
    client: TestClient, token: str, gw: list
):
    nb = _create(client, token)
    nb_id = nb["id"]

    r = client.post(
        f"{API}/{nb_id}/run",
        json={
            "cell_id": "cell-1",
            "source": (
                "df = get_bars('EURUSD', 'H1', 5)\n"
                "len(df)"
            ),
        },
        headers=_auth(token),
    )
    body = r.json()
    assert body["ok"] is True, body
    assert body["result_repr"] == "5"
    assert gw and gw[0] == ("EURUSD", "H1", 5)

    # time column converted to tz-aware UTC datetimes (ns/s unit is
    # pandas-version-dependent — only the UTC tz matters)
    r = client.post(
        f"{API}/{nb_id}/run",
        json={"cell_id": "cell-1", "source": "str(df['time'].dtype)"},
        headers=_auth(token),
    )
    body = r.json()
    assert body["ok"] is True, body
    assert body["result_repr"] is not None
    assert "UTC" in body["result_repr"]
    assert "datetime64" in body["result_repr"]

    # non-literal args → guiding runtime error (no prefetch happened)
    r = client.post(
        f"{API}/{nb_id}/run",
        json={
            "cell_id": "cell-1",
            "source": "sym = 'GBPUSD'\nget_bars(sym, 'H1', 7)",
        },
        headers=_auth(token),
    )
    body = r.json()
    assert body["ok"] is False
    assert body["etype"] == "RuntimeError"
    assert "未预取" in (body["message"] or "")
    assert all(c[0] == "EURUSD" for c in gw)
    notebook_kernel.reset_session(uuid.UUID(nb_id))


def test_run_chart_event(client: TestClient, token: str, gw: list):
    nb = _create(client, token)
    r = client.post(
        f"{API}/{nb['id']}/run",
        json={
            "cell_id": "cell-1",
            "source": (
                "df = get_bars('EURUSD', 'H1', 10)\n"
                "plot(df['close'], title='收盘价')\n"
                "'plotted'"
            ),
        },
        headers=_auth(token),
    )
    body = r.json()
    assert body["ok"] is True, body
    assert body["result_repr"] == "'plotted'"
    assert len(body["charts"]) == 1
    chart = body["charts"][0]
    assert chart["title"] == "收盘价"
    assert chart["series"][0]["name"] == "收盘价"
    assert len(chart["series"][0]["points"]) == 10
    assert chart["series"][0]["points"][0]["value"] == pytest.approx(1.105)
    notebook_kernel.reset_session(uuid.UUID(nb["id"]))


def test_restart_clears_state(client: TestClient, token: str):
    nb = _create(client, token)
    nb_id = nb["id"]

    r = client.post(
        f"{API}/{nb_id}/run",
        json={"cell_id": "cell-1", "source": "q = 100"},
        headers=_auth(token),
    )
    assert r.json()["ok"] is True

    r = client.post(f"{API}/{nb_id}/restart", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r = client.post(
        f"{API}/{nb_id}/run",
        json={"cell_id": "cell-1", "source": "q"},
        headers=_auth(token),
    )
    body = r.json()
    assert body["ok"] is False
    assert body["etype"] == "NameError"
    notebook_kernel.reset_session(uuid.UUID(nb_id))


def test_run_disabled_returns_409(client: TestClient, token: str, monkeypatch):
    nb = _create(client, token)
    monkeypatch.setattr(settings, "NOTEBOOK_ENABLED", False)
    r = client.post(
        f"{API}/{nb['id']}/run",
        json={"cell_id": "cell-1", "source": "1"},
        headers=_auth(token),
    )
    assert r.status_code == 409


def test_run_oversize_source_422(client: TestClient, token: str):
    nb = _create(client, token)
    r = client.post(
        f"{API}/{nb['id']}/run",
        json={"cell_id": "cell-1", "source": "x = '" + "a" * 200_001 + "'"},
        headers=_auth(token),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Kernel service unit-level
# ---------------------------------------------------------------------------


def test_sweep_idle_sessions_evicts_only_expired(monkeypatch):
    nb_id = uuid.uuid4()
    fresh = notebook_kernel._Session(lock=None, last_used=time.monotonic())  # type: ignore[arg-type]
    stale = notebook_kernel._Session(lock=None, last_used=time.monotonic() - 10_000)  # type: ignore[arg-type]
    notebook_kernel._sessions[nb_id] = stale
    other = uuid.uuid4()
    notebook_kernel._sessions[other] = fresh
    try:
        swept = notebook_kernel.sweep_idle_sessions()
        assert swept >= 1
        assert nb_id not in notebook_kernel._sessions
        assert other in notebook_kernel._sessions
    finally:
        notebook_kernel._sessions.pop(nb_id, None)
        notebook_kernel._sessions.pop(other, None)


def test_extract_bars_requests_literal_variants(monkeypatch):
    monkeypatch.setattr(settings, "NOTEBOOK_MAX_BARS_PER_CALL", 50_000)
    src = (
        "a = get_bars('EURUSD', 'H1', 100)\n"
        "b = get_bars('eurusd', timeframe='h1', count=100)\n"  # same key → deduped
        "c = get_bars('GBPUSD', 'M15')\n"
        "d = get_bars(sym, 'H1', 10)\n"  # non-literal → skipped
    )
    reqs = {r[0]: r for r in notebook_kernel.extract_bars_requests(src)}
    assert set(reqs) == {"EURUSD", "GBPUSD"}
    assert reqs["EURUSD"] == ("EURUSD", "H1", 100)
    assert reqs["GBPUSD"] == ("GBPUSD", "M15", 500)


# ---------------------------------------------------------------------------
# Backtest queue overview (M18 任务队列监控)
# ---------------------------------------------------------------------------


def test_queue_overview_shape_and_route_order(client: TestClient, token: str):
    r = client.get("/api/v1/backtests/queue/overview", headers=_auth(token))
    assert r.status_code == 200, r.text  # literal route wins over /{task_id}
    body = r.json()
    assert isinstance(body["running"], list)
    assert isinstance(body["pending_count"], int)
    assert body["pending_count"] >= 0
    assert body["oldest_pending_created_at"] is None or isinstance(
        body["oldest_pending_created_at"], str
    )


def test_queue_overview_requires_auth(client: TestClient):
    r = client.get("/api/v1/backtests/queue/overview")
    assert r.status_code == 401
