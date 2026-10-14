"""Tests for ``/api/v1/gateway/*`` (node registry + health logs).

Node creation probes a (dead) port so the offline-on-failure path is
covered live; health-log rows are inserted via raw asyncpg because
assertions must not reuse TestClient's loop-bound connections.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

API = "/api/v1/gateway"

NODE_FIELDS = {
    "id", "name", "type", "host", "port", "status", "version",
    "uptime_seconds", "active_sessions", "cpu_percent", "memory_percent",
    "last_heartbeat", "is_primary", "metadata", "created_at",
}

LOG_FIELDS = {
    "id", "node_id", "status", "latency_ms", "error_rate", "message",
    "recorded_at",
}


@pytest.fixture()
def token(client: TestClient) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"gw-{uuid.uuid4().hex[:8]}@test.com",
            "username": f"gw{uuid.uuid4().hex[:8]}",
            "password": "Str0ngPass!x",
            "full_name": "网关测试",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _promote(client: TestClient, token: str, role: str = "admin") -> None:
    """把这个令牌的用户提成 admin。

    注册/删除网关节点是管理员操作（2.6：注册表就是「平台认可的网关」名单，
    而平台会给名单里每个地址送 X-Gateway-Key）。首个注册用户虽然是天然的
    admin，但同一个测试库里别的用例早就注册过用户了——必须显式提权，否则
    这些用例跑不跑得通取决于执行顺序。
    """
    from jose import jwt
    from sqlalchemy import update

    from app.config import settings
    from app.engine.session import AsyncSessionLocal
    from app.models import User

    uid = jwt.decode(token, settings.JWT_SECRET_KEY,
                     algorithms=[settings.JWT_ALGORITHM])["sub"]

    async def _up() -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(update(User).where(User.id == uuid.UUID(uid))
                             .values(role=role))
            await db.commit()
    client.portal.call(_up)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_gateway_requires_token(client: TestClient) -> None:
    assert client.get(f"{API}/nodes").status_code == 401
    assert client.get(f"{API}/health-logs").status_code == 401


def test_create_node_probe_failure_goes_offline(
    client: TestClient, token: str
) -> None:
    # 探活一个必然拒绝连接的端口 → 201 但 status=offline
    _promote(client, token)
    r = client.post(
        f"{API}/nodes",
        json={"name": "死节点", "host": "127.0.0.1", "port": 9},
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == NODE_FIELDS
    assert body["status"] == "offline"
    assert body["last_heartbeat"] is None
    assert body["name"] == "死节点"

    # 重复 host+port → 409
    r = client.post(
        f"{API}/nodes",
        json={"name": "重复", "host": "127.0.0.1", "port": 9},
        headers=_auth(token),
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "该网关节点已存在"

    # 列表包含该节点
    r = client.get(f"{API}/nodes", headers=_auth(token))
    assert r.status_code == 200
    assert any(n["id"] == body["id"] for n in r.json()["items"])


def test_delete_node_and_404(client: TestClient, token: str) -> None:
    _promote(client, token)
    r = client.post(
        f"{API}/nodes",
        json={"name": "待删", "host": "127.0.0.1", "port": 10},
        headers=_auth(token),
    )
    assert r.status_code == 201
    node_id = r.json()["id"]

    assert client.delete(f"{API}/nodes/{node_id}", headers=_auth(token)).status_code == 204
    assert client.delete(f"{API}/nodes/{node_id}", headers=_auth(token)).status_code == 404
    r = client.get(f"{API}/nodes", headers=_auth(token))
    assert all(n["id"] != node_id for n in r.json()["items"])


def test_delete_node_guards_primary_and_bound_accounts(
    client: TestClient, token: str
) -> None:
    # 有绑定账户的节点 → 409（M8：防止级联静默解绑账户）
    _promote(client, token)
    r = client.post(
        f"{API}/nodes",
        json={"name": "带账户", "host": "127.0.0.1", "port": 12},
        headers=_auth(token),
    )
    assert r.status_code == 201
    node_id = r.json()["id"]

    user_id = client.get("/api/v1/auth/me", headers=_auth(token)).json()["id"]

    async def _bind_account() -> str:
        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account

        async with AsyncSessionLocal() as db:
            acct = MT5Account(
                login=880000 + (uuid.uuid4().int % 90000),
                server="MetaQuotes-Demo", name="守卫测试",
                user_id=uuid.UUID(user_id),
                gateway_node_id=uuid.UUID(node_id),
            )
            db.add(acct)
            await db.commit()
            return str(acct.id)

    acct_id = client.portal.call(_bind_account)

    blocked = client.delete(f"{API}/nodes/{node_id}", headers=_auth(token))
    assert blocked.status_code == 409
    assert "绑定" in blocked.json()["detail"]

    # 解绑后可删
    async def _unbind() -> None:
        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account

        async with AsyncSessionLocal() as db:
            acct = await db.get(MT5Account, uuid.UUID(acct_id))
            await db.delete(acct)
            await db.commit()

    client.portal.call(_unbind)
    assert client.delete(f"{API}/nodes/{node_id}", headers=_auth(token)).status_code == 204

    # 主节点不可删
    nodes = client.get(f"{API}/nodes", headers=_auth(token)).json()["items"]
    primary = next((n for n in nodes if n["is_primary"]), None)
    if primary is not None:
        r = client.delete(f"{API}/nodes/{primary['id']}", headers=_auth(token))
        assert r.status_code == 409
        assert "主网关节点" in r.json()["detail"]


def test_health_logs_order_and_limits(client: TestClient, token: str) -> None:
    # 建节点 → 拿 node_id
    _promote(client, token)
    r = client.post(
        f"{API}/nodes",
        json={"name": "日志节点", "host": "127.0.0.1", "port": 11},
        headers=_auth(token),
    )
    node_id = r.json()["id"]

    # raw asyncpg 插入两条日志（recorded_at DESC 排序依据）
    import asyncpg

    from tests.conftest import TEST_DATABASE_URL

    async def _insert() -> None:
        conn = await asyncpg.connect(TEST_DATABASE_URL.replace("+asyncpg", ""))
        try:
            await conn.execute(
                "INSERT INTO gateway_health_logs (id, node_id, status, latency_ms, "
                "error_rate, message, recorded_at) VALUES "
                "($1, $2, 'online', 12.5, 0.0, NULL, now() - interval '1 minute'), "
                "($3, $2, 'offline', 88.0, 1.0, '行情网关不可达', now())",
                uuid.uuid4(), uuid.UUID(node_id), uuid.uuid4(),
            )
        finally:
            await conn.close()

    asyncio.run(_insert())

    r = client.get(
        f"{API}/health-logs", params={"node_id": node_id}, headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert [i["status"] for i in body["items"]] == ["offline", "online"]  # DESC
    assert set(body["items"][0]) == LOG_FIELDS
    assert body["items"][0]["message"] == "行情网关不可达"

    # limit=1 截断
    r = client.get(
        f"{API}/health-logs",
        params={"node_id": node_id, "limit": 1},
        headers=_auth(token),
    )
    assert len(r.json()["items"]) == 1

    # limit 越界 → 422
    r = client.get(f"{API}/health-logs", params={"limit": 501}, headers=_auth(token))
    assert r.status_code == 422
