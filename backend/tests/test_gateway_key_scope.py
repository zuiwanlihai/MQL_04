"""2.6 —— 平台网关密钥只发往**已注册**的节点。

``X-Gateway-Key`` 是平台级共享密钥：网关侧不做账户级鉴权，拿到它的人就能对每一
台网关下单、平仓、改白名单。而它此前是「构造客户端就带」——任何一处能把请求发
往用户自填地址的代码，都会把密钥原样送到那台主机。这样的路径有两条：

* ``POST /accounts/test`` 的 ``gateway_url``：任意登录用户、任意主机。
* ``POST /gateway/nodes`` 的 ``host``/``port``：任意登录用户，而且探活之后这个
  地址会被写进注册表，心跳每几秒再送一次——一次填写，长期外泄。

修法是把凭据改成**选择加入**：显式传 URL 构造的客户端不带任何凭据，只有
``for_registered_node()``（注册表里的节点）和平台自配地址才带。这样漏写标志的
后果是一次显眼的 401，而不是一次静默的密钥外泄——失效方向指向「不可用」，
而不是「泄露」。

配套地，「注册节点」必须是管理员操作：否则「已注册」根本不构成信任边界，
任何人都能先注册一台自己的主机，再坐等心跳把密钥送过去。

这里用 127.0.0.1 上的真实 HTTP 服务收请求头——密钥有没有真的上网，只有真发一
次才算数。httpx 尊重 HTTP_PROXY 之类的环境变量，所以夹具里先把代理清掉，
免得本机代理把请求截走、让断言莫名其妙地失败。
"""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.config import settings
from app.services.gateway_client import GatewayClient

PLATFORM_KEY = "platform-shared-gateway-key"

# 一份同时能满足 /health 与 /account/login 的响应体：探活要 status，登录要
# login/server/balance 这一族字段（_map_probe 对缺失字段是有默认值的）。
_BODY = {
    "status": "ok", "version": "1.0.0-capture", "uptime_seconds": 1.0,
    "cpu_percent": 0.0, "memory_percent": 0.0, "active_sessions": 1,
    "login": 1, "server": "CaptureSrv", "name": "capture", "currency": "USD",
    "balance": 0.0, "equity": 0.0, "margin": 0.0, "margin_free": 0.0,
    "margin_level": 0.0, "leverage": 100, "trade_allowed": True,
}


class _CaptureServer:
    """真实的 loopback HTTP 服务，逐条记录收到的请求头。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _respond(self) -> None:
                # 先把请求体读干净再回包。不回读的话，本端关闭连接时对端
                # 可能还在写 body——Windows 回环上这一下是 RST，客户端读到
                # 「连接被重置」（httpx ReadError），端点于是回 503，测试
                # 偶发翻红。实测不回读 300 次里错 35 次，回读后 0 次。
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                outer.requests.append((self.path, dict(self.headers)))
                body = json.dumps(_BODY).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            # 这三个名字是 BaseHTTPRequestHandler 的派发协议规定的，改不得。
            do_GET = do_POST = do_DELETE = _respond  # noqa: N815

            def log_message(self, *args) -> None:  # 别把访问日志灌进 pytest 输出
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port: int = self._srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self) -> _CaptureServer:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._srv.shutdown()
        self._srv.server_close()
        self._thread.join(timeout=5)

    def keys(self) -> list[str]:
        """收到过的 X-Gateway-Key（缺失记为空串，便于整体断言）。"""
        return [h.get("X-Gateway-Key", "") for _, h in self.requests]

    def paths(self) -> list[str]:
        return [p for p, _ in self.requests]


@pytest.fixture
def capture(monkeypatch):
    """一台 loopback 捕获服务 + 一份已知的平台密钥。"""
    monkeypatch.setattr(settings, "GATEWAY_API_KEY", PLATFORM_KEY)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
                "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    with _CaptureServer() as srv:
        yield srv


# ── 传输层：密钥到底有没有上网 ─────────────────────────────────


async def test_user_supplied_url_is_contacted_without_any_credential(capture):
    """用户填的地址照常访问，但一个字节的凭据都不带。"""
    client = GatewayClient(capture.url)
    try:
        await client.health()
    finally:
        await client.aclose()

    assert capture.paths() == ["/health"], "探活本身要照常发出"
    assert capture.keys() == [""], (
        f"用户自填的 {capture.url} 收到了平台网关密钥——"
        f"这等于把「对所有网关下单」的权限交给任意一个填地址的人"
    )


async def test_registered_node_client_still_carries_the_platform_key(capture):
    """注册表里的节点照旧带密钥——不能为了堵漏把正常路径也打死。"""
    client = GatewayClient.for_registered_node(capture.url)
    try:
        await client.health()
    finally:
        await client.aclose()

    assert capture.keys() == [PLATFORM_KEY]


async def test_platform_configured_gateway_still_carries_the_key(capture, monkeypatch):
    """无参构造＝平台自己 .env 里配的那台网关，当然是「自己的」。"""
    monkeypatch.setattr(settings, "GATEWAY_URL", capture.url)
    client = GatewayClient()
    try:
        await client.health()
    finally:
        await client.aclose()

    assert capture.keys() == [PLATFORM_KEY]


async def test_contacting_a_user_url_does_not_fall_back_to_the_platform_gateway(
    capture, monkeypatch,
):
    """地址只以用户给的那个为准：不能悄悄回落到平台网关再带上密钥。"""
    monkeypatch.setattr(settings, "GATEWAY_URL", "http://127.0.0.1:1")  # 打不通
    client = GatewayClient(capture.url)
    try:
        await client.health()
    finally:
        await client.aclose()
    assert capture.keys() == [""]


# ── 接口层：真实端点走一遍 ────────────────────────────────────


def _register(client, *, role: str = "trader") -> dict:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"gk-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"gk{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "密钥范围测试"},
    )
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    if role == "admin":
        from jose import jwt
        from sqlalchemy import update

        from app.engine.session import AsyncSessionLocal
        from app.models import User

        uid = jwt.decode(token, settings.JWT_SECRET_KEY,
                         algorithms=[settings.JWT_ALGORITHM])["sub"]

        async def _up() -> None:
            async with AsyncSessionLocal() as db:
                await db.execute(update(User).where(User.id == uuid.UUID(uid))
                                 .values(role="admin"))
                await db.commit()
        client.portal.call(_up)
    return {"Authorization": f"Bearer {token}"}


def test_account_probe_of_a_user_supplied_url_sends_no_key(client, capture):
    """POST /accounts/test 的 gateway_url：探测照做，密钥留下。"""
    headers = _register(client)
    r = client.post(
        "/api/v1/accounts/test",
        headers=headers,
        json={"login": 12345, "password": "pw", "server": "CaptureSrv",
              "gateway_url": capture.url},
    )
    assert r.status_code == 200, r.text
    assert capture.keys() == [""], "测试连接不能把平台密钥送到用户填的地址"


def test_creating_a_node_probes_it_without_the_key(client, capture):
    """POST /gateway/nodes：注册前的探活同样不带凭据。

    这条比 /accounts/test 更要紧——节点一旦注册成功，心跳会**持续**向该地址
    发送请求，一次泄露会变成长期泄露。
    """
    headers = _register(client, role="admin")
    r = client.post(
        "/api/v1/gateway/nodes",
        headers=headers,
        json={"name": "捕获节点", "host": "127.0.0.1", "port": capture.port},
    )
    assert r.status_code == 201, r.text
    assert capture.keys() == [""], "注册探活不能把平台密钥送到待验证的主机"
    try:
        assert capture.paths() == ["/health"], "注册时该探一次 /health"
    finally:
        client.delete(f"/api/v1/gateway/nodes/{r.json()['id']}", headers=headers)


def test_node_registration_and_deletion_require_admin(client, capture):
    """注册/删除节点是平台级操作：普通用户不能借它把主机拉进注册表。

    没有这道闸，「只发往已注册节点」就是句空话——任何人都能先把自己那台
    注册进来，然后坐等心跳把密钥送上门。
    """
    headers = _register(client)  # trader

    r = client.post(
        "/api/v1/gateway/nodes",
        headers=headers,
        json={"name": "偷偷注册", "host": "127.0.0.1", "port": capture.port},
    )
    assert r.status_code == 403, r.text
    assert capture.keys() == [], "被拒的注册不该发出任何请求"

    d = client.delete(f"/api/v1/gateway/nodes/{uuid.uuid4()}", headers=headers)
    assert d.status_code == 403, d.text
