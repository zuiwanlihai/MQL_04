"""2.7 —— 对象级授权：别人的东西，看得见不等于动得了。

风控中心此前只验**角色**（admin/trader），不验**归属**：

* 任何 trader 都能改、能删**别人账户上的**风控规则——包括把别人的
  `daily_max_loss` 关掉；「绕过风控」不只是把自己那套调松，还可以去动别人的。
* `create_rule` 直接采信请求体里的 `account_id`/`run_id`，可以把规则挂到别人
  账户上；`scope=global` 的规则对**所有人**生效（引擎里 global 与 account/run
  是并集），任何 trader 都能给全平台加一条。
* 任何登录用户都能把任意登录号加进/移出**网关级的**交易白名单——移出去就是
  让别人的账户下不了单。

按已定决策，这里划两条不同的线：

* **读**（告警、风控事件列表）：保持平台级。运维信号藏起来比露出来更危险——
  看不到的 kill_switch 等于陷阱。告警列表因此不动。
* **写**：按归属。规则/告警已读/白名单都只允许动自己的；平台级对象（global 规则、
  无归属的告警）归管理员。

归属判定沿用仓内既有约定（`manual_trade._owned_account`）：`user_id is None`
的账户视为共享，人人可用；越权一律 **404 而非 403**——403 等于告诉你「这个 id
真实存在，只是不是你的」。
"""

from __future__ import annotations

import uuid

import pytest

API_LIVE = "/api/v1/live"
API_TRADE = "/api/v1/trade"


# ── helpers ────────────────────────────────────────────────────


def _register(client, *, role: str = "trader") -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"oa-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"oa{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "授权测试"},
    )
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    if role != "trader":
        _set_role(client, token, role)
    return token


def _set_role(client, token: str, role: str) -> None:
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


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _seed_account_run(client, token: str) -> dict:
    """给这个用户建一个 MT5 账户 + 一个实盘运行（规则要挂在这上面）。"""

    async def _ins() -> dict:
        from decimal import Decimal

        from jose import jwt
        from sqlalchemy import select

        from app.config import settings
        from app.engine.session import AsyncSessionLocal
        from app.models import GatewayNode, LiveRun, MT5Account, Strategy

        uid = uuid.UUID(jwt.decode(token, settings.JWT_SECRET_KEY,
                                   algorithms=[settings.JWT_ALGORITHM])["sub"])
        async with AsyncSessionLocal() as db:
            node = (await db.execute(
                select(GatewayNode).where(GatewayNode.host == "127.0.0.1",
                                          GatewayNode.port == 8090)
            )).scalars().first()
            if node is None:  # uq(host, port)：整个测试库只留一个本地节点
                node = GatewayNode(host="127.0.0.1", port=8090, status="online")
                db.add(node)
                await db.flush()
            strat = Strategy(user_id=uid, name=f"oa-{uuid.uuid4().hex[:8]}",
                             timeframe="H1", status="active")
            db.add(strat)
            # 登录号在 60 万段里**避重**取号：测试库是所有用例共享的一份，
            # uq(login, server) 撞上就是整条用例以 IntegrityError 翻红——
            # 纯随机取号撞 792609 已经这样翻过一次车。
            used = set((await db.execute(
                select(MT5Account.login).where(
                    MT5Account.login.between(600000, 689999))
            )).scalars().all())
            login = next(x for x in range(600000, 690000) if x not in used)
            acct = MT5Account(
                login=login,
                server="MetaQuotes-Demo", name="t", account_type="demo",
                leverage=100, currency="USD", gateway_node_id=node.id,
                user_id=uid, live_allowed=True,
            )
            db.add(acct)
            await db.flush()
            run = LiveRun(
                user_id=uid, strategy_id=strat.id, account_id=acct.id,
                source_code="x", symbol="EURUSD", timeframe="H1",
                execution_mode="standard", allocated_capital=Decimal("1000"),
                leverage=100, magic=100 + (uuid.uuid4().int % 900),
                positions=[], status="running",
                balance=Decimal("1000"), equity=Decimal("1000"),
                margin_free=Decimal("1000"),
            )
            db.add(run)
            await db.commit()
            return {"account_id": str(acct.id), "run_id": str(run.id)}
    return client.portal.call(_ins)


def _mk_rule(client, token: str, *, account_id: str | None = None,
             run_id: str | None = None, scope: str = "account",
             rtype: str = "daily_max_loss", action: str = "reject") -> dict:
    body: dict = {"scope": scope, "type": rtype, "threshold": 100,
                  "action": action}
    if account_id:
        body["account_id"] = account_id
    if run_id:
        body["run_id"] = run_id
    r = client.post(f"{API_LIVE}/rules", json=body, headers=_auth(token))
    return {"status": r.status_code, "body": r.json() if r.content else {}}


def _insert_alert(client, *, account_id: str | None, run_id: str | None = None,
                  title: str = "测试告警", source: str = "reconcile") -> str:
    async def _ins() -> str:
        from app.engine.session import AsyncSessionLocal
        from app.models import AlertEvent

        async with AsyncSessionLocal() as db:
            row = AlertEvent(
                level="critical", source=source, title=title, message="m",
                account_id=uuid.UUID(account_id) if account_id else None,
                run_id=uuid.UUID(run_id) if run_id else None,
            )
            db.add(row)
            await db.commit()
            return str(row.id)
    return client.portal.call(_ins)


def _alert_read(client, alert_id: str) -> bool:
    async def _q() -> bool:
        from app.engine.session import AsyncSessionLocal
        from app.models import AlertEvent

        async with AsyncSessionLocal() as db:
            row = await db.get(AlertEvent, uuid.UUID(alert_id))
            return bool(row.is_read)
    return client.portal.call(_q)


def _rule_gone(client, rule_id: str) -> bool:
    async def _q() -> bool:
        from app.engine.session import AsyncSessionLocal
        from app.models import RiskRule

        async with AsyncSessionLocal() as db:
            return await db.get(RiskRule, uuid.UUID(rule_id)) is None
    return client.portal.call(_q)


def _rule_threshold(client, rule_id: str) -> float:
    async def _q() -> float:
        from app.engine.session import AsyncSessionLocal
        from app.models import RiskRule

        async with AsyncSessionLocal() as db:
            row = await db.get(RiskRule, uuid.UUID(rule_id))
            return float(row.threshold)
    return client.portal.call(_q)


# ── 风控规则：列表按归属 ───────────────────────────────────────


def test_rule_list_hides_other_users_rules(client):
    a, b = _register(client), _register(client)
    seed_a = _seed_account_run(client, a)
    rule = _mk_rule(client, a, account_id=seed_a["account_id"])
    assert rule["status"] == 201, rule

    # 不带过滤：看不到别人的规则
    body = client.get(f"{API_LIVE}/rules", headers=_auth(b)).json()
    assert all(r["id"] != rule["body"]["id"] for r in body), \
        "别人的风控规则不该出现在我的列表里"

    # 显式点名别人的账户：同样看不到（这正是「对象级」的那条线）
    body = client.get(f"{API_LIVE}/rules",
                      params={"account_id": seed_a["account_id"]},
                      headers=_auth(b)).json()
    assert body == [], "指定别人的 account_id 也不能把规则读出来"

    # 本人照常看得到
    body = client.get(f"{API_LIVE}/rules", headers=_auth(a)).json()
    assert any(r["id"] == rule["body"]["id"] for r in body)


def test_global_rules_stay_visible_to_everyone(client):
    """全局规则对所有人**生效**，所以必须对所有人可见——藏起来就是陷阱。"""
    admin = _register(client, role="admin")
    viewer = _register(client)
    g = _mk_rule(client, admin, scope="global", rtype="max_lot_per_order")
    assert g["status"] == 201, g

    body = client.get(f"{API_LIVE}/rules", headers=_auth(viewer)).json()
    assert any(r["id"] == g["body"]["id"] for r in body), \
        "全局规则在拦所有人的单，却对它拦的人不可见"


# ── 风控规则：写按归属 ─────────────────────────────────────────


def test_trader_cannot_touch_another_users_rule(client):
    a, b = _register(client), _register(client)
    seed_a = _seed_account_run(client, a)
    rule = _mk_rule(client, a, account_id=seed_a["account_id"])
    rid = rule["body"]["id"]

    r = client.patch(f"{API_LIVE}/rules/{rid}", json={"threshold": 1},
                     headers=_auth(b))
    assert r.status_code == 404, f"别人的规则被改了：{r.status_code} {r.text}"
    assert _rule_threshold(client, rid) == 100.0

    r = client.delete(f"{API_LIVE}/rules/{rid}", headers=_auth(b))
    assert r.status_code == 404, f"别人的规则被删了：{r.status_code}"
    assert not _rule_gone(client, rid)


def test_owner_still_manages_own_rule(client):
    a = _register(client)
    seed_a = _seed_account_run(client, a)
    rid = _mk_rule(client, a, account_id=seed_a["account_id"])["body"]["id"]

    assert client.patch(f"{API_LIVE}/rules/{rid}", json={"threshold": 5},
                        headers=_auth(a)).status_code == 200
    assert _rule_threshold(client, rid) == 5.0
    assert client.delete(f"{API_LIVE}/rules/{rid}",
                         headers=_auth(a)).status_code == 204


def test_admin_still_manages_any_rule(client):
    a = _register(client)
    admin = _register(client, role="admin")
    seed_a = _seed_account_run(client, a)
    rid = _mk_rule(client, a, account_id=seed_a["account_id"])["body"]["id"]

    assert client.patch(f"{API_LIVE}/rules/{rid}", json={"threshold": 7},
                        headers=_auth(admin)).status_code == 200
    assert _rule_threshold(client, rid) == 7.0


def test_trader_cannot_attach_rule_to_another_users_account(client):
    a, b = _register(client), _register(client)
    seed_a = _seed_account_run(client, a)

    r = _mk_rule(client, b, account_id=seed_a["account_id"])
    assert r["status"] == 404, f"规则被挂到别人账户上了：{r}"

    r = _mk_rule(client, b, run_id=seed_a["run_id"], scope="run")
    assert r["status"] == 404, f"规则被挂到别人的运行上了：{r}"


def test_creating_a_global_rule_requires_admin(client):
    """global 规则对所有人生效——一条规则改的是整个平台的默认风控。"""
    b = _register(client)
    r = _mk_rule(client, b, scope="global", rtype="max_lot_per_order")
    assert r["status"] == 403, f"普通用户改动了全平台风控：{r}"


# ── 告警：平台级可见，但已读只能动自己的 ───────────────────────


def test_alert_list_stays_platform_level(client):
    """按决策，告警列表保持平台级：运维信号不按用户过滤。"""
    a, b = _register(client), _register(client)
    seed_a = _seed_account_run(client, a)
    aid = _insert_alert(client, account_id=seed_a["account_id"])

    body = client.get(f"{API_LIVE}/alerts", headers=_auth(b)).json()
    assert any(x["id"] == aid for x in body["items"])


def test_cannot_mark_another_users_alert_read(client):
    a, b = _register(client), _register(client)
    seed_a = _seed_account_run(client, a)
    aid = _insert_alert(client, account_id=seed_a["account_id"])

    r = client.post(f"{API_LIVE}/alerts/{aid}/read", headers=_auth(b))
    assert r.status_code == 404, f"别人的告警被标已读了：{r.status_code}"
    assert _alert_read(client, aid) is False

    assert client.post(f"{API_LIVE}/alerts/{aid}/read",
                       headers=_auth(a)).status_code == 200
    assert _alert_read(client, aid) is True


def test_read_all_only_touches_my_own_alerts(client):
    """「全部已读」= 全部**我的**：不能顺手把别人的（和平台的）也清了。"""
    a, b = _register(client), _register(client)
    seed_a = _seed_account_run(client, a)
    seed_b = _seed_account_run(client, b)
    mine = _insert_alert(client, account_id=seed_b["account_id"], title="我的")
    theirs = _insert_alert(client, account_id=seed_a["account_id"], title="别人的")
    platform = _insert_alert(client, account_id=None, title="平台的",
                             source="gateway")

    r = client.post(f"{API_LIVE}/alerts/read-all", headers=_auth(b))
    assert r.status_code == 200, r.text

    assert _alert_read(client, mine) is True
    assert _alert_read(client, theirs) is False, "别人的告警被一起标已读了"
    assert _alert_read(client, platform) is False, \
        "平台级告警（网关异常等）归运维，普通用户不能替全体清掉"


def test_admin_read_all_covers_platform_alerts(client):
    admin = _register(client, role="admin")
    platform = _insert_alert(client, account_id=None, title="平台的",
                             source="gateway")
    assert client.post(f"{API_LIVE}/alerts/read-all",
                       headers=_auth(admin)).status_code == 200
    assert _alert_read(client, platform) is True


# ── 交易白名单：只能管自己的登录号 ─────────────────────────────


class _StubWhitelist:
    """网关白名单桩：记录调用，不真的连网关。"""

    def __init__(self, *a, **k) -> None:
        self.calls: list[tuple] = []

    async def trade_whitelist(self) -> dict:
        return {"trade_enabled": True, "logins": []}

    async def trade_whitelist_add(self, login: int) -> dict:
        self.calls.append(("add", login))
        return {"trade_enabled": True, "logins": [login]}

    async def trade_whitelist_remove(self, login: int) -> dict:
        self.calls.append(("remove", login))
        return {"trade_enabled": True, "logins": []}

    async def aclose(self) -> None:
        pass

    @classmethod
    def for_registered_node(cls, *a, **k):
        return cls()


@pytest.fixture
def wl_stub(monkeypatch):
    import app.api.v1.endpoints.trade as tr_mod

    stub = _StubWhitelist()
    monkeypatch.setattr(tr_mod, "GatewayClient", lambda *a, **k: stub)
    return stub


def test_cannot_whitelist_another_users_login(client, wl_stub):
    """顶栏「加入白名单」是自助功能，但只能加**自己绑定的**登录号。"""
    a, b = _register(client), _register(client)
    seed_a = _seed_account_run(client, a)
    login_a = _login_of(client, seed_a["account_id"])

    mine = client.get("/api/v1/accounts", headers=_auth(b)).json()["items"]
    assert mine == [], "前置：b 名下没有账户"

    r = client.post(f"{API_TRADE}/whitelist", json={"login": login_a},
                    headers=_auth(b))
    assert r.status_code == 403, f"别人的登录号被加进了白名单：{r.status_code}"
    assert wl_stub.calls == [], "被拒的请求不该到达网关"

    r = client.delete(f"{API_TRADE}/whitelist/{login_a}", headers=_auth(b))
    assert r.status_code == 403, "别人的登录号被移出了白名单（等于让人下不了单）"
    assert wl_stub.calls == []


def test_owner_and_admin_can_manage_the_whitelist(client, wl_stub):
    a = _register(client)
    admin = _register(client, role="admin")
    seed_a = _seed_account_run(client, a)
    login_a = _login_of(client, seed_a["account_id"])

    r = client.post(f"{API_TRADE}/whitelist", json={"login": login_a},
                    headers=_auth(a))
    assert r.status_code == 200, r.text
    assert wl_stub.calls == [("add", login_a)]

    # 管理员可以管平台上任意登录号（例如尚未绑定的终端）
    r = client.post(f"{API_TRADE}/whitelist", json={"login": 999999},
                    headers=_auth(admin))
    assert r.status_code == 200, r.text


def _login_of(client, account_id: str) -> int:
    async def _q() -> int:
        from app.engine.session import AsyncSessionLocal
        from app.models import MT5Account

        async with AsyncSessionLocal() as db:
            return int((await db.get(MT5Account, uuid.UUID(account_id))).login)
    return client.portal.call(_q)
