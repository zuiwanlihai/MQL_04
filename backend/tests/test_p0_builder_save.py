"""P0 完整验证：原子保存 + 乐观锁 + source_code_hash 更新。"""
from __future__ import annotations

import hashlib
import uuid

from fastapi.testclient import TestClient

STRAT = "/api/v1/strategies"


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _reg(client: TestClient) -> str:
    s = uuid.uuid4().hex[:8]
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"p0_{s}@test.com",
            "username": f"p0_{s}",
            "password": "Passw0rd!",
            "full_name": "P0 验证",
        },
    )
    assert r.status_code in (200, 201), f"注册: {r.status_code} {r.text}"
    return r.json()["access_token"]


def _mk_strat(client: TestClient, hdrs: dict) -> dict:
    # 用合法策略骨架（满足 AST validator 的回调要求）
    skeleton = (
        'def on_init(ctx):\n'
        '    ctx.declare(symbol="XAUUSD", period="H1", params={})\n'
        '\n'
        'def on_bar(ctx, bar):\n'
        '    pass\n'
        '\n'
        'def on_order(ctx, order):\n'
        '    pass\n'
        '\n'
        'def on_stop(ctx, reason):\n'
        '    pass\n'
    )
    r = client.post(
        STRAT + "/",
        json={
            "name": f"P0_{uuid.uuid4().hex[:6]}",
            "description": "原子保存验证",
            "direction": "both",
            "symbol_ids": ["XAUUSD"],
            "timeframe": "H1",
            "source_code": skeleton,
            "builder_config": None,
        },
        headers=hdrs,
    )
    assert r.status_code == 201, f"创建策略: {r.status_code} {r.text}"
    sid = r.json()["id"]
    return client.get(f"{STRAT}/{sid}/", headers=hdrs).json()


# ──────────────────────────────────────────────────────────────────────


def test_p0_1_builder_save_atomic(client: TestClient):
    """原子保存：source_code + builder_config + 元数据 + hash 单次写入"""
    hdrs = _auth(_reg(client))
    s = _mk_strat(client, hdrs)
    sid, ua = s["id"], s["updated_at"]
    new_src = s["source_code"] + "# builder-save appended\n"
    new_bc = {
        "version": 1, "symbol": "XAUUSD", "timeframe": "H1",
        "volume": 0.1, "direction": "both",
        "indicators": [], "rules": [],
    }

    r = client.put(
        f"{STRAT}/{sid}/builder-save",
        json={
            "source_code": new_src,
            "builder_config": new_bc,
            "direction": "long_only",
            "symbol_ids": ["XAUUSD", "EURUSD"],
            "timeframe": "M15",
            "base_updated_at": ua,
        },
        headers=hdrs,
    )
    assert r.status_code == 200, f"builder-save: {r.status_code} {r.text}"
    d = r.json()

    # hash 正确
    assert d["source_code_hash"] == hashlib.sha256(new_src.encode()).hexdigest()
    # 元数据都更新了
    assert d["builder_config"] == new_bc
    assert d["direction"] == "long_only"
    assert d["timeframe"] == "M15"
    # updated_at 变了（下次乐观锁要用）
    assert d["updated_at"] != ua
    # GET 也带 hash
    d2 = client.get(f"{STRAT}/{sid}/", headers=hdrs).json()
    assert d2["source_code_hash"] == d["source_code_hash"]


def test_p0_2_builder_save_optimistic_lock(client: TestClient):
    """过期 base_updated_at → 409"""
    hdrs = _auth(_reg(client))
    s = _mk_strat(client, hdrs)

    r = client.put(
        f"{STRAT}/{s['id']}/builder-save",
        json={
            "source_code": s["source_code"],
            "builder_config": {
                "version": 1, "symbol": "XAUUSD", "timeframe": "H1",
                "volume": 0.1, "direction": "both",
                "indicators": [], "rules": [],
            },
            "base_updated_at": "2020-01-01T00:00:00",
        },
        headers=hdrs,
    )
    assert r.status_code == 409, f"期望 409, 得 {r.status_code}: {r.text}"


def test_p0_3_save_source_updates_hash(client: TestClient):
    """编辑器手动改源码 → PUT /{id}/source 也更新 source_code_hash"""
    hdrs = _auth(_reg(client))
    s = _mk_strat(client, hdrs)
    modified = s["source_code"] + "# manual edit\n"

    r = client.put(
        f"{STRAT}/{s['id']}/source",
        json={"source_code": modified, "base_updated_at": s["updated_at"]},
        headers=hdrs,
    )
    assert r.status_code == 200
    # GET 验证 hash 已更新
    d = client.get(f"{STRAT}/{s['id']}/", headers=hdrs).json()
    assert d["source_code_hash"] == hashlib.sha256(modified.encode()).hexdigest()


def test_p0_4_patch_optimistic_lock(client: TestClient):
    """PATCH /{id} 也加了乐观锁"""
    hdrs = _auth(_reg(client))
    s = _mk_strat(client, hdrs)

    r = client.patch(
        f"{STRAT}/{s['id']}/",
        json={"name": "hacked", "base_updated_at": "2020-01-01T00:00:00"},
        headers=hdrs,
    )
    assert r.status_code == 409, f"PATCH 乐观锁期望 409, 得 {r.status_code}"
