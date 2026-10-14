"""Strategy marketplace API tests (PRD Phase 3 策略市场).

Snapshot freeze semantics, legal disclaimer gate, browse visibility,
import-copy flow, soft-remove idempotency, defensive re-validation,
audit coverage, and CHECK-constraint metadata sync (模型/迁移/测试三处同步).
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import CheckConstraint

API = "/api/v1/market/listings"
STRAT_API = "/api/v1/strategies"

STRATEGY_SRC = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_bar(ctx, bar):
    if ctx.storage.get("opened"):
        return
    ctx.storage["opened"] = True
    ctx.buy("EURUSD", 0.1)

def on_order(ctx, order):
    pass
"""

OTHER_SRC = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={})

def on_bar(ctx, bar):
    ctx.close("EURUSD", 0.1)

def on_order(ctx, order):
    pass
"""


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _token(client: TestClient, tag: str = "mk") -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"{tag}-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"{tag}{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "市场测试"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _create_strategy(client: TestClient, token: str, *, name: str | None = None,
                     source: str | None = STRATEGY_SRC,
                     symbol_ids: list[str] | None = None,
                     timeframe: str = "H1") -> str:
    body: dict = {
        "name": name or f"mk-{uuid.uuid4().hex[:8]}",
        "language": "python",
        "direction": "both",
        "symbol_ids": symbol_ids if symbol_ids is not None else ["EURUSD"],
        "timeframe": timeframe,
        "tags": ["trend"],
    }
    if source is not None:
        body["source_code"] = source
    r = client.post(STRAT_API, json=body, headers=_auth(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _publish(client: TestClient, token: str, strategy_id: str, **over):
    body = {"strategy_id": strategy_id, "disclaimer_ack": True}
    body.update(over)
    return client.post(API, json=body, headers=_auth(token))


def _audit_count(client: TestClient, action: str) -> int:
    async def _q():
        from sqlalchemy import func as sfunc
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog

        async with AsyncSessionLocal() as db:
            return (await db.execute(
                select(sfunc.count()).select_from(AuditLog)
                .where(AuditLog.action == action)
            )).scalar_one()
    return client.portal.call(_q)


# ── publish: snapshot / ownership / validation / dedup ────────

def test_publish_roundtrip_and_snapshot_freeze(client):
    token = _token(client)
    sid = _create_strategy(client, token)
    r = _publish(client, token, sid)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["strategy_id"] == sid
    assert body["source_code"] == STRATEGY_SRC
    assert body["import_count"] == 0
    assert body["author_username"] != ""
    assert "source_code" in body  # 详情级响应（发布返回 ListingDetailOut）
    listing_id = body["id"]

    # 后续修改原策略绝不影响市场条目（冻结快照）
    upd = client.patch(f"{STRAT_API}/{sid}", json={"name": "renamed-original"},
                       headers=_auth(token))
    assert upd.status_code == 200
    src = client.put(f"{STRAT_API}/{sid}/source",
                     json={"source_code": OTHER_SRC}, headers=_auth(token))
    assert src.status_code == 200
    detail = client.get(f"{API}/{listing_id}", headers=_auth(token)).json()
    assert detail["strategy_name"] != "renamed-original"
    assert detail["source_code"] == STRATEGY_SRC


def test_publish_requires_owner(client):
    owner, other = _token(client, "own"), _token(client, "oth")
    sid = _create_strategy(client, owner)
    r = _publish(client, other, sid)
    assert r.status_code == 404


def test_publish_rejects_invalid_source(client):
    token = _token(client)
    # 空 source 创建（不传 source_code）→ validator 判缺回调 → 422
    sid = _create_strategy(client, token, source=None)
    r = _publish(client, token, sid)
    assert r.status_code == 422
    assert r.json()["detail"]["ok"] is False


def test_publish_requires_disclaimer_ack(client):
    token = _token(client)
    sid = _create_strategy(client, token)
    assert _publish(client, token, sid, disclaimer_ack=False).status_code == 422
    body = {"strategy_id": sid}
    assert client.post(API, json=body, headers=_auth(token)).status_code == 422


def test_publish_duplicate_hash_409_and_republish_after_remove(client):
    token = _token(client)
    sid = _create_strategy(client, token)
    first = _publish(client, token, sid)
    assert first.status_code == 201
    dup = _publish(client, token, sid)
    assert dup.status_code == 409

    # 同作者发布不同源码的新策略不受影响
    sid2 = _create_strategy(client, token, source=OTHER_SRC)
    assert _publish(client, token, sid2).status_code == 201

    # 下架后允许重新发布同内容
    assert client.delete(f"{API}/{first.json()['id']}",
                         headers=_auth(token)).status_code == 204
    republish = _publish(client, token, sid)
    assert republish.status_code == 201


# ── browse: visibility / no source leak / filters ─────────────

def test_browse_visible_to_all_and_no_source_code_leak(client):
    a, b = _token(client, "ba"), _token(client, "bb")
    name = f"vis-{uuid.uuid4().hex[:8]}"
    sid = _create_strategy(client, a, name=name)
    pub = _publish(client, a, sid)
    assert pub.status_code == 201

    # user B（未发布任何内容）能看到 user A 的条目
    lst = client.get(API, params={"q": name}, headers=_auth(b))
    assert lst.status_code == 200
    items = lst.json()["items"]
    assert len(items) == 1
    assert items[0]["strategy_name"] == name
    assert "source_code" not in items[0]
    assert items[0]["author_username"] != ""


def test_browse_filters_symbol_timeframe_and_popular_sort(client):
    a = _token(client, "bf")
    tag = uuid.uuid4().hex[:8]
    eur = _create_strategy(client, a, name=f"eur-{tag}", symbol_ids=["EURUSD"])
    gbp = _create_strategy(client, a, name=f"gbp-{tag}", symbol_ids=["GBPUSD"],
                           source=OTHER_SRC)  # 同作者防重：源码必须不同
    le, lg = _publish(client, a, eur), _publish(client, a, gbp)
    assert le.status_code == 201 and lg.status_code == 201

    # symbol 筛选
    lst = client.get(API, params={"q": tag, "symbol": "EURUSD"},
                     headers=_auth(a))
    assert [i["strategy_name"] for i in lst.json()["items"]] == [f"eur-{tag}"]

    # timeframe 筛选（源码加注释变体，避免同作者同 hash 409）
    sid_h4 = _create_strategy(client, a, name=f"h4-{tag}", timeframe="H4",
                              source=STRATEGY_SRC + f"\n# variant-h4-{tag}\n")
    assert _publish(client, a, sid_h4).status_code == 201
    lst = client.get(API, params={"q": tag, "timeframe": "H4"}, headers=_auth(a))
    assert [i["strategy_name"] for i in lst.json()["items"]] == [f"h4-{tag}"]

    # sort=popular：把 eur 的 import_count 直接抬高（只验证排序语义）
    async def _bump():
        from sqlalchemy import update

        from app.engine.session import AsyncSessionLocal
        from app.models import MarketListing

        async with AsyncSessionLocal() as db:
            await db.execute(
                update(MarketListing)
                .where(MarketListing.id == uuid.UUID(le.json()["id"]))
                .values(import_count=5)
            )
            await db.commit()
    client.portal.call(_bump)
    lst = client.get(API, params={"q": tag, "sort": "popular"}, headers=_auth(a))
    names = [i["strategy_name"] for i in lst.json()["items"]]
    assert names.index(f"eur-{tag}") < names.index(f"gbp-{tag}")


def test_detail_contains_source_code(client):
    token = _token(client)
    sid = _create_strategy(client, token)
    listing_id = _publish(client, token, sid).json()["id"]
    detail = client.get(f"{API}/{listing_id}", headers=_auth(token))
    assert detail.status_code == 200
    assert detail.json()["source_code"] == STRATEGY_SRC
    assert detail.json()["author_username"] != ""


# ── remove: soft delete / permission / idempotent ─────────────

def test_remove_hidden_and_idempotent_and_permission(client):
    a, b = _token(client, "ra"), _token(client, "rb")
    sid = _create_strategy(client, a)
    listing_id = _publish(client, a, sid).json()["id"]

    # 非作者下架 → 404（不暴露存在性）
    assert client.delete(f"{API}/{listing_id}", headers=_auth(b)).status_code == 404

    # 作者下架 → 204，列表/详情不可见
    assert client.delete(f"{API}/{listing_id}", headers=_auth(a)).status_code == 204
    assert client.get(f"{API}/{listing_id}", headers=_auth(a)).status_code == 404
    lst = client.get(API, params={"q": "mk-"}, headers=_auth(a)).json()
    assert all(i["id"] != listing_id for i in lst["items"])

    # 重复下架幂等 204
    assert client.delete(f"{API}/{listing_id}", headers=_auth(a)).status_code == 204


# ── import: copy / count / conflict / defensive validation ────

def test_import_creates_copy_and_counts(client):
    a, b = _token(client, "ia"), _token(client, "ib")
    sid = _create_strategy(client, a)
    pub = _publish(client, a, sid)
    assert pub.status_code == 201
    listing_id = pub.json()["id"]

    r = client.post(f"{API}/{listing_id}/import",
                    json={"name": f"copy-{uuid.uuid4().hex[:6]}"},
                    headers=_auth(b))
    assert r.status_code == 201, r.text
    copy_id = r.json()["id"]
    detail = client.get(f"{STRAT_API}/{copy_id}", headers=_auth(b))
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "draft"
    assert body["source_code"] == STRATEGY_SRC
    assert body["direction"] == "both"
    assert body["symbol_ids"] == ["EURUSD"]
    assert body["timeframe"] == "H1"

    # import_count 只对原 listing +1
    after = client.get(f"{API}/{listing_id}", headers=_auth(a)).json()
    assert after["import_count"] == 1


def test_import_name_conflict_and_removed_404(client):
    a, b = _token(client, "ca"), _token(client, "cb")
    sid = _create_strategy(client, a)
    listing_id = _publish(client, a, sid).json()["id"]

    # B 已有同名策略 → 409
    same = f"dup-{uuid.uuid4().hex[:6]}"
    _create_strategy(client, b, name=same)
    r = client.post(f"{API}/{listing_id}/import", json={"name": same},
                    headers=_auth(b))
    assert r.status_code == 409

    # 下架后导入 → 404
    assert client.delete(f"{API}/{listing_id}", headers=_auth(a)).status_code == 204
    r = client.post(f"{API}/{listing_id}/import",
                    json={"name": f"after-{uuid.uuid4().hex[:6]}"},
                    headers=_auth(b))
    assert r.status_code == 404


def test_import_defensive_revalidation(client):
    a, b = _token(client, "da"), _token(client, "db")
    sid = _create_strategy(client, a)
    listing_id = _publish(client, a, sid).json()["id"]

    # 模拟 DB 直改污染冻结源码 → 导入端防御性二次校验拦截
    async def _tamper():
        from app.engine.session import AsyncSessionLocal
        from app.models import MarketListing

        async with AsyncSessionLocal() as db:
            row = await db.get(MarketListing, uuid.UUID(listing_id))
            row.source_code = "def broken(:\n    this is not python"
            await db.commit()
    client.portal.call(_tamper)

    r = client.post(f"{API}/{listing_id}/import",
                    json={"name": f"safe-{uuid.uuid4().hex[:6]}"},
                    headers=_auth(b))
    assert r.status_code == 422
    assert r.json()["detail"]["ok"] is False


# ── audit coverage ────────────────────────────────────────────

def test_audit_actions_recorded(client):
    a = _token(client, "aa")
    b = _token(client, "ab")
    sid = _create_strategy(client, a)

    before = {act: _audit_count(client, act) for act in
              ("market.listing_publish", "market.listing_remove",
               "market.listing_import")}

    listing_id = _publish(client, a, sid).json()["id"]
    assert client.post(f"{API}/{listing_id}/import",
                       json={"name": f"aud-{uuid.uuid4().hex[:6]}"},
                       headers=_auth(b)).status_code == 201
    assert client.delete(f"{API}/{listing_id}", headers=_auth(a)).status_code == 204

    after = {act: _audit_count(client, act) for act in before}
    assert after["market.listing_publish"] == before["market.listing_publish"] + 1
    assert after["market.listing_import"] == before["market.listing_import"] + 1
    assert after["market.listing_remove"] == before["market.listing_remove"] + 1


# ── schema/migration sync guard ───────────────────────────────

def test_check_constraints_in_metadata():
    """迁移库 CHECK 与模型元数据同步（防『测试全绿但真实库 500』漂移）。"""
    from app.models import MarketListing

    checks = {
        c.name: str(c.sqltext)
        for c in MarketListing.__table__.constraints
        if isinstance(c, CheckConstraint) and c.name
    }
    assert "ck_market_listings_status" in checks
    assert "'active'" in checks["ck_market_listings_status"]
    assert "'removed'" in checks["ck_market_listings_status"]
    assert "ck_market_listings_direction" in checks
    assert "'long_only'" in checks["ck_market_listings_direction"]
    assert "'both'" in checks["ck_market_listings_direction"]
