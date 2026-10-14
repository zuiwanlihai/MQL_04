"""2.5 — 网关密钥为空时必须拒绝启动，而不是放行所有人。

发运的 ``.env.example`` 里是 ``GATEWAY_API_KEY=``（空）。pydantic 会用这个空值
覆盖默认值，于是 ``compare_digest("", "")`` 对空请求头返回 True——照着安装文档
做出来的网关，是一个任何同网段的人都能下单、平仓的明文端口。

「密钥没配好」的正确失效方向是**不开门**：网关手上没有凭据可比对时，它无法区分
合法调用者和攻击者，此时唯一安全的动作是拒绝。

这里用一条只挂 ``require_gateway_key`` 的最小路由来验它，不碰真实路由与 MT5
客户端——依赖本身的行为才是这两条防线的主体。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.security import assert_key_configured, require_gateway_key  # noqa: E402

TEST_KEY = "test-gateway-key"


def _probe_client() -> TestClient:
    """一条只会经过密钥依赖的路由。"""
    app = FastAPI()

    @app.get("/probe")
    async def probe(_: None = Depends(require_gateway_key)) -> dict:
        return {"ok": True}

    return TestClient(app)


def test_key_has_no_default_value():
    """配置项不得带默认密钥：默认值就是写在源码里的公开口令。"""
    from app.config import GatewaySettings

    default = GatewaySettings.model_fields["GATEWAY_API_KEY"].default
    assert default == "", f"网关密钥有默认值 {default!r}——那等于把口令写进源码"


def test_configured_key_starts(monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_API_KEY", TEST_KEY)
    assert_key_configured()  # 不抛


def test_blank_key_refuses_startup(monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_API_KEY", "")
    with pytest.raises(RuntimeError) as ei:
        assert_key_configured()
    assert "GATEWAY_API_KEY" in str(ei.value)
    assert "拒绝启动" in str(ei.value)


def test_whitespace_only_key_is_not_configured(monkeypatch):
    """只有空格/换行的值同样是没配——不能靠一个空格糊过去。"""
    monkeypatch.setattr(settings, "GATEWAY_API_KEY", "   \n")
    with pytest.raises(RuntimeError):
        assert_key_configured()


# ── 请求级兜底 ──────────────────────────────────────────────────


def test_blank_key_rejects_instead_of_allowing(monkeypatch):
    """即使进程带着空密钥活着，请求也必须被拒，而不是空头侥幸通过。"""
    monkeypatch.setattr(settings, "GATEWAY_API_KEY", "")
    c = _probe_client()

    assert c.get("/probe", headers={"X-Gateway-Key": ""}).status_code == 503
    assert c.get("/probe").status_code == 503


def test_valid_key_passes_and_wrong_key_is_401(monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_API_KEY", TEST_KEY)
    c = _probe_client()

    assert c.get("/probe", headers={"X-Gateway-Key": TEST_KEY}).status_code == 200
    assert c.get("/probe", headers={"X-Gateway-Key": "nope"}).status_code == 401
    assert c.get("/probe").status_code == 401


def test_non_ascii_configured_key_does_not_500(monkeypatch):
    """非 ASCII 密钥不能让 compare_digest 抛 TypeError。

    ``secrets.compare_digest`` 对含非 ASCII 的 ``str`` 会直接抛异常——而密钥是
    人写的配置（中文口令完全可能），调用方送来的却是任意头。不编码的话，一次
    猜错口令的请求会变成 500：既泄露了「这个值碰不得」的信号，也让 401 这条
    正常路径在真实部署里失效。
    """
    monkeypatch.setattr(settings, "GATEWAY_API_KEY", "中文密钥-123")
    c = _probe_client()

    assert c.get("/probe", headers={"X-Gateway-Key": "wrong"}).status_code == 401
    assert c.get("/probe", headers={"X-Gateway-Key": ""}).status_code == 401
