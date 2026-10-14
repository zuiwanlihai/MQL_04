"""Shared-secret header check (``X-Gateway-Key``).

``/health`` is intentionally exempt — the backend heartbeat must be able to
read it even if the key drifts, and it exposes nothing sensitive.

「密钥没配好」的正确失效方向是**不开门**：网关手上没有凭据可比对时，它无法区分
合法调用者和攻击者，此时唯一安全的动作是拒绝——启动时拒绝启动，运行中拒绝请求。
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from app.config import settings


def configured_key() -> str:
    """当前配置的网关密钥（去掉首尾空白）；未配置时返回空串。"""
    return settings.GATEWAY_API_KEY.strip()


def assert_key_configured() -> None:
    """启动自检：密钥缺失时拒绝启动。

    发运的 ``.env.example`` 曾经把 ``GATEWAY_API_KEY=`` 留空，而 pydantic 会用这
    个空值覆盖默认值——于是 ``compare_digest("", "")`` 对空请求头返回 True。
    照着安装文档做出来的网关，就是一个任何同网段的人都能下单、平仓的明文端口。
    密钥是这套部署唯一的门，门没装上就不该开门营业。
    """
    if configured_key():
        return
    raise RuntimeError(
        "GATEWAY_API_KEY 未配置，网关拒绝启动：密钥为空时所有接口都等于无鉴权。"
        "请在 gateway/.env 中设置——推荐用 "
        "`python -m app.pairing --server http://<后端地址> --code <配对码>` "
        "自动写入，或手工填一段随机串，并与后端 .env 的 GATEWAY_API_KEY 一致。"
    )


async def require_gateway_key(
    x_gateway_key: str | None = Header(default=None, alias="X-Gateway-Key"),
) -> None:
    """Reject requests carrying a missing or wrong gateway key."""
    expected = configured_key()
    if not expected:
        # 兜底：启动自检本该拦住这种配置，但「进程还活着 + 密钥被改空」不该变成
        # 一次静默放行。503 而不是 401——这不是调用方的问题，是服务没配好。
        raise HTTPException(status_code=503, detail="网关未配置访问密钥，已拒绝请求")
    # 两侧都编码成 bytes：``compare_digest`` 对含非 ASCII 的 str 会抛 TypeError，
    # 于是一个非 ASCII 请求头就能把 401 变成 500。编码后它只会安静地不相等。
    if x_gateway_key is None or not secrets.compare_digest(
        x_gateway_key.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="网关密钥无效")
