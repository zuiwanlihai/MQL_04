"""Backend ops passthrough + abnormal-restart consumption tests (M11 Phase B)."""

from __future__ import annotations

import uuid

import pytest

API = "/api/v1/gateway"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(client) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"ops-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"ops{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "运维测试"},
    )
    assert r.status_code == 201
    return r.json()["access_token"]


class StubOpsGateway:
    def __init__(self, base_url=None, *a, **k):
        # M12: heartbeat probes EVERY node; only the default node carries the
        # abnormal-restart flag here so residual nodes from other test files
        # don't multiply the alert count.
        self._default = not base_url or base_url.endswith("127.0.0.1:8090")

    @classmethod
    def for_registered_node(cls, base_url=None, *a, **k):
        return cls(base_url, *a, **k)

    async def selfcheck(self):
        return {"all_pass": False, "checked_at": "2026-10-11T10:00:00+00:00",
                "items": [{"key": "algo_trade", "label": "允许算法交易",
                           "status": "fail", "detail": "未勾选", "fix_hint": "终端设置"}]}

    async def power_fix(self, execute=False):
        if execute:
            return {"executed": True, "commands": ["powercfg /change standby-timeout-ac 0"],
                    "results": [{"cmd": "x", "rc": 0, "out": ""}], "detail": "电源方案已修复"}
        return {"executed": False, "commands": ["powercfg /change standby-timeout-ac 0"],
                "detail": "指引模式"}

    async def health(self):
        return {"status": "ok", "version": "1.0.0", "uptime_seconds": 10,
                "cpu_percent": 1.0, "memory_percent": 2.0, "active_sessions": 1,
                "abnormal_restart": self._default,
                "abnormal_restart_info": {"pid": 111, "started_at_iso": "2026-10-11T09:00:00Z"},
                "guard_gave_up": False}

    async def aclose(self):
        pass


class StubOpsGateway2(StubOpsGateway):
    """Same abnormal flag — for the dedup second tick."""


def test_selfcheck_passthrough(client, token, monkeypatch):
    from app.api.v1.endpoints import gateway as gw_mod

    monkeypatch.setattr(gw_mod, "GatewayClient", StubOpsGateway)
    r = client.get(f"{API}/selfcheck", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["all_pass"] is False
    assert body["items"][0]["key"] == "algo_trade"


def test_power_fix_guidance_default_and_audit(client, token, monkeypatch):
    from app.api.v1.endpoints import gateway as gw_mod

    monkeypatch.setattr(gw_mod, "GatewayClient", StubOpsGateway)
    r = client.post(f"{API}/power/fix", json={}, headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["executed"] is False

    async def _audit():
        from sqlalchemy import func, select

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog

        async with AsyncSessionLocal() as db:
            return (await db.execute(
                select(func.count()).select_from(AuditLog)
                .where(AuditLog.action == "gateway_power_fix")
            )).scalar_one()

    assert client.portal.call(_audit) >= 1


def test_abnormal_restart_alerts_once_per_event(client, monkeypatch):
    """heartbeat_once: first tick alerts+audits; repeat flag does NOT re-alert."""
    import app.services.gateway_heartbeat as hb

    monkeypatch.setattr(hb, "GatewayClient", StubOpsGateway)
    alerts: list = []
    audits: list = []

    # P0-4: 生产代码走 gate_fault().emit() → 直接 monkeypatch FaultRecord.emit()
    # 绕开延迟 import / 模块缓存 / alert/audit 内部调用等所有问题
    async def fake_fault_emit(self, db=None, source="fault_state", level="warn"):
        alerts.append(f"{self.fault_type.value} → {self.fault_state.value}")
        audits.append(f"fault_{self.fault_type.value}")
    monkeypatch.setattr(
        "app.services.fault_state.FaultRecord.emit", fake_fault_emit, raising=False,
    )

    async def _run():
        from app.engine.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            await hb.heartbeat_once(db)
            await hb.heartbeat_once(db)  # same abnormal event → deduped
            await hb.heartbeat_once(db)
        return len(alerts), len(audits)

    n_alerts, n_audits = client.portal.call(_run)
    assert n_alerts == 1  # PRD: 显著提示，但不无限重复轰炸
    assert n_audits == 1
