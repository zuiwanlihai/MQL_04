"""Synthetic dataset 模式的虚拟下单 API 层测试。

测试点：
1. Schema model_validator：gateway 模式必须传 account_id；synthetic 模式必须传 synthetic_dataset_id
2. Endpoint：synthetic 模式跳过 gateway/账户校验；正确创建 run 行（data_source + synthetic_dataset_id）
3. Endpoint：synthetic 模式下 synthetic_dataset_id 不存在 → 422
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

API = "/api/v1/paper"
DS_API = "/api/v1/synthetic-datasets"
STRAT_API = "/api/v1/strategies"

STRATEGY_SRC = """
def on_init(ctx):
    ctx.declare(symbol="XAUUSD", period="M1", params={})

def on_bar(ctx, bar):
    if ctx.storage.get("opened"):
        return
    ctx.storage["opened"] = True
    ctx.buy("XAUUSD", 0.01)

def on_order(ctx, order):
    pass
"""


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(client: TestClient) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"synth-{uuid.uuid4().hex[:10]}@test.com",
            "username": f"synth{uuid.uuid4().hex[:10]}",
            "password": "Passw0rd!",
            "full_name": "合成盘测试",
        },
    )
    assert r.status_code == 201
    return r.json()["access_token"]


@pytest.fixture
def synth_strategy(client: TestClient, token: str) -> str:
    r = client.post(
        STRAT_API, headers=_auth(token),
        json={"name": f"synth-{uuid.uuid4().hex[:6]}",
              "source_code": STRATEGY_SRC, "params_schema": {}},
    )
    assert r.status_code == 201
    return r.json()["id"]


@pytest.fixture
def synth_ds(client: TestClient, token: str) -> int:
    """造一个最小合成数据集。"""
    r = client.post(
        DS_API, headers=_auth(token),
        json={
            "name": f"paper-fixture-{uuid.uuid4().hex[:6]}",
            "symbol": "XAUUSD",
            "base_price": 1900.0,
            "volatility": 0.0005,
            "first_time": 1735689600,
            "last_time": 1735690200,
            "bar_count": 10,
            "bars": [
                {"time": 1735689600 + i * 60,
                 "open": 1900.0, "high": 1900.1,
                 "low": 1899.9, "close": 1900.05,
                 "volume": 10}
                for i in range(10)
            ],
        },
    )
    print(f"[FIXTURE synth_ds] status={r.status_code} body={r.text[:300]}")
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── Schema 校验 ────────────────────────────────────────────────────────

def test_gateway_mode_requires_account_id(client: TestClient, token: str, synth_strategy: str):
    r = client.post(
        API, headers=_auth(token),
        json={
            "strategy_id": synth_strategy,
            "symbol": "EURUSD", "timeframe": "M1",
            "initial_deposit": 10000, "leverage": 100,
            "assumption_ack": True,
        },
    )
    assert r.status_code == 422
    assert "网关模式必须提供绑定的 MT5 账户" in r.text


def test_synthetic_mode_requires_dataset_id(client: TestClient, token: str, synth_strategy: str):
    r = client.post(
        API, headers=_auth(token),
        json={
            "strategy_id": synth_strategy,
            "data_source": "synthetic",
            "symbol": "XAUUSD", "timeframe": "M1",
            "initial_deposit": 10000, "leverage": 100,
            "assumption_ack": True,
        },
    )
    assert r.status_code == 422
    assert "合成数据集模式必须提供 synthetic_dataset_id" in r.text


# ── Endpoint synthetic 端到端 ──────────────────────────────────────────

def test_synthetic_creates_run_row(client: TestClient, token: str,
                                   synth_strategy: str, synth_ds: int):
    """正常 synthetic 创建：run 行 data_source + synthetic_dataset_id 正确。"""
    # 先确认 gateway 模式能正常 create（排除路由问题）
    # r = client.post(...) gateway mode... 但我们没有 MT5 account fixture

    # 先查一下路由是否存在
    r0 = client.options('/api/v1/paper')
    print(f'OPTIONS /paper: {r0.status_code}')

    r = client.post(
        API, headers=_auth(token),
        json={
            "strategy_id": synth_strategy,
            "data_source": "synthetic",
            "synthetic_dataset_id": synth_ds,  # 数据集主键是 Integer
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "initial_deposit": 10000, "leverage": 100,
            "assumption_ack": True,
        },
    )
    print(f"STATUS: {r.status_code}")
    print(f"BODY: {r.text[:500]}")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["data_source"] == "synthetic"
    assert body["synthetic_dataset_id"] == synth_ds


def test_synthetic_unknown_dataset_422(client: TestClient, token: str, synth_strategy: str):
    """synthetic_dataset_id 不存在 → 422。"""
    r = client.post(
        API, headers=_auth(token),
        json={
            "strategy_id": synth_strategy,
            "data_source": "synthetic",
            "synthetic_dataset_id": 999999,
            "symbol": "XAUUSD", "timeframe": "M1",
            "initial_deposit": 10000, "leverage": 100,
            "assumption_ack": True,
        },
    )
    assert r.status_code == 422
    assert "不存在" in r.text
