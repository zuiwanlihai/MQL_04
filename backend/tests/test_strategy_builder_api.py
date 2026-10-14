"""Visual strategy builder API tests (P2): builder_config round-trip.

构建器产物链路 = POST /strategies 携带 builder_config + source_code →
GET detail 回读；PATCH builder_config 单独更新；exclude_unset 语义保证
PATCH 不带该字段时不清空。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

API = "/api/v1/strategies"

BUILDER_CONFIG = {
    "version": 1,
    "symbol": "EURUSD",
    "timeframe": "H1",
    "volume": 0.1,
    "direction": "both",
    "indicators": [
        {"id": "ind_1", "kind": "MA", "period": 5},
        {"id": "ind_2", "kind": "MA", "period": 20},
    ],
    "rules": [
        {
            "id": "rule_1",
            "op": "cross_above",
            "left": {"type": "indicator", "indicatorId": "ind_1", "output": "value"},
            "right": {"type": "indicator", "indicatorId": "ind_2", "output": "value"},
            "open": "buy",
            "close": None,
        },
    ],
}

GENERATED_SOURCE = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={"volume": 0.1})


def on_bar(ctx, bar):
    pass


def on_order(ctx, order):
    pass
"""


@pytest.fixture
def token(client: TestClient) -> str:
    email = f"builder-{uuid.uuid4().hex[:10]}@test.com"
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "username": f"builder{uuid.uuid4().hex[:10]}",
            "password": "Passw0rd!",
            "full_name": "构建器测试",
        },
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_create_with_builder_config_roundtrip(client, token):
    resp = client.post(
        API,
        json={
            "name": f"vc-{uuid.uuid4().hex[:8]}",
            "direction": "both",
            "symbol_ids": ["EURUSD"],
            "timeframe": "H1",
            "source_code": GENERATED_SOURCE,
            "builder_config": BUILDER_CONFIG,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201
    body = resp.json()
    # 列表/创建响应不含 builder_config（与 source_code 同口径）
    assert "builder_config" not in body

    detail = client.get(f"{API}/{body['id']}", headers=_auth(token))
    assert detail.status_code == 200
    data = detail.json()
    assert data["source_code"] == GENERATED_SOURCE
    assert data["builder_config"] == BUILDER_CONFIG


def test_create_without_builder_config_returns_null(client, token):
    resp = client.post(
        API, json={"name": f"plain-{uuid.uuid4().hex[:8]}"}, headers=_auth(token)
    )
    assert resp.status_code == 201
    detail = client.get(f"{API}/{resp.json()['id']}", headers=_auth(token))
    assert detail.json()["builder_config"] is None


def test_patch_builder_config_and_exclude_unset(client, token):
    s = client.post(
        API,
        json={
            "name": f"patch-{uuid.uuid4().hex[:8]}",
            "source_code": GENERATED_SOURCE,
            "builder_config": BUILDER_CONFIG,
        },
        headers=_auth(token),
    ).json()

    # 更新：改手数
    updated = {**BUILDER_CONFIG, "volume": 0.2}
    resp = client.patch(
        f"{API}/{s['id']}", json={"builder_config": updated}, headers=_auth(token)
    )
    assert resp.status_code == 200
    detail = client.get(f"{API}/{s['id']}", headers=_auth(token))
    assert detail.json()["builder_config"]["volume"] == 0.2

    # exclude_unset：PATCH 不带 builder_config 不得清空
    client.patch(
        f"{API}/{s['id']}", json={"description": "只是改描述"}, headers=_auth(token)
    )
    detail = client.get(f"{API}/{s['id']}", headers=_auth(token))
    assert detail.json()["builder_config"]["volume"] == 0.2

    # 显式 null 清除绑定
    client.patch(
        f"{API}/{s['id']}", json={"builder_config": None}, headers=_auth(token)
    )
    detail = client.get(f"{API}/{s['id']}", headers=_auth(token))
    assert detail.json()["builder_config"] is None


def test_template_takes_priority_over_source_code(client, token):
    tpl_resp = client.get(f"{API}/templates", headers=_auth(token))
    tpl_id = tpl_resp.json()["items"][0]["id"]
    tpl_source = client.get(f"{API}/templates/{tpl_id}", headers=_auth(token)).json()[
        "source_code"
    ]
    resp = client.post(
        API,
        json={
            "name": f"prio-{uuid.uuid4().hex[:8]}",
            "template_id": tpl_id,
            "source_code": "def broken(): pass",
            "builder_config": BUILDER_CONFIG,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201
    detail = client.get(f"{API}/{resp.json()['id']}", headers=_auth(token))
    assert detail.json()["source_code"] == tpl_source


def test_builder_config_rejected_for_non_dict(client, token):
    resp = client.post(
        API,
        json={
            "name": f"bad-{uuid.uuid4().hex[:8]}",
            "builder_config": ["not", "a", "dict"],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422
