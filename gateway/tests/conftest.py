"""网关测试公共夹具。

``GATEWAY_API_KEY`` 没有默认值（默认值＝写进源码的公开密钥），所以网关启动自检
会拒绝空密钥。每个用例都必须自己给一个——这正是生产路径要求的「显式配置」，
测试不该靠一个内置口令绕开它。

另外把 ``gateway/`` 挂上 ``sys.path``：测试文件各自也做了一次，但 conftest 先于
它们导入，不挂的话这里的 ``from app.config import ...`` 会 ImportError。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

TEST_GATEWAY_KEY = "test-gateway-key"


@pytest.fixture(autouse=True)
def gateway_api_key(monkeypatch) -> str:
    """给每个用例一份已配置的密钥；需要空密钥的用例自行覆盖。"""
    monkeypatch.setattr(settings, "GATEWAY_API_KEY", TEST_GATEWAY_KEY)
    return TEST_GATEWAY_KEY
