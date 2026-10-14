"""FaultState / FaultRecord / StateTransition 单元测试。

覆盖优化方案 P0-4 要求：
1. FaultState 枚举与 gateway_heartbeat 的 online/degraded/offline 兼容
2. FaultRecord 结构化输出的 5 字段完整性
3. RecoveryTemplate 映射表覆盖所有 FaultType
4. state_transition 辅助函数
5. gate_fault 便捷工厂
6. 不改 DB schema 的约束（FaultRecord 不涉及 ORM model）
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.services.fault_state import (
    FaultRecord,
    FaultState,
    FaultType,
    StateTransition,
    gate_fault,
    state_transition,
)

# ── FaultState 枚举 ─────────────────────────────────────────────


def test_fault_state_all_six_present():
    assert FaultState.RUNNING.value == "running"
    assert FaultState.RECONNECTING.value == "reconnecting"
    assert FaultState.DEGRADED.value == "degraded"
    assert FaultState.CIRCUIT_BREAKING.value == "circuit_breaking"
    assert FaultState.FAILED.value == "failed"
    assert FaultState.EXITING.value == "exiting"


def test_fault_state_matches_gateway_heartbeat_statuses():
    """gateway_heartbeat 用 online/degraded/offline/status 字符串，
    FaultState.matches 应该能正确映射。"""
    assert FaultState.RUNNING.matches("online")
    assert FaultState.DEGRADED.matches("degraded")
    assert FaultState.FAILED.matches("offline")
    assert FaultState.RECONNECTING.matches("starting")
    # 反向也成立
    assert not FaultState.RUNNING.matches("offline")


# ── FaultType 枚举 ──────────────────────────────────────────────


def test_fault_type_all_have_recovery_template():
    """每个 FaultType 都有恢复建议模板，不遗漏。"""
    # 触发 default_recovery，如果有模板就返回非空字符串
    for ft in FaultType:
        record = FaultRecord(
            fault_type=ft,
            fault_state=FaultState.FAILED,
            related_object={"run_id": "test"},
            action_taken="test",
        )
        assert record.default_recovery(), f"{ft.value} 缺少恢复建议模板"


# ── FaultRecord 结构化输出 ───────────────────────────────────────


def test_fault_record_five_required_fields():
    record = FaultRecord(
        fault_type=FaultType.GATEWAY_DISCONNECTED,
        fault_state=FaultState.RECONNECTING,
        related_object={"run_id": "abc-123", "node_id": "nw-1"},
        action_taken="进入重连等待",
    )
    assert record.fault_type == FaultType.GATEWAY_DISCONNECTED
    assert record.fault_state == FaultState.RECONNECTING
    assert record.related_object == {"run_id": "abc-123", "node_id": "nw-1"}
    assert record.action_taken == "进入重连等待"
    assert isinstance(record.timestamp, datetime)
    assert record.timestamp.tzinfo is not None  # UTC aware


def test_fault_record_autofills_recovery_from_template():
    record = FaultRecord(
        fault_type=FaultType.STRATEGY_CRASHED,
        fault_state=FaultState.FAILED,
        related_object={},
        action_taken="终止运行",
    )
    assert "查看运行日志定位异常" in record.recovery_suggestion


def test_fault_record_recovery_override_overrides_template():
    record = FaultRecord(
        fault_type=FaultType.STRATEGY_CRASHED,
        fault_state=FaultState.FAILED,
        related_object={},
        action_taken="终止运行",
        recovery_suggestion="这是手动覆盖的恢复建议",
    )
    assert record.recovery_suggestion == "这是手动覆盖的恢复建议"


def test_fault_record_to_audit_params_shape():
    record = FaultRecord(
        fault_type=FaultType.ORDER_FAILED,
        fault_state=FaultState.DEGRADED,
        related_object={"run_id": "r1"},
        action_taken="暂停新下单",
        detail="保证金不足",
    )
    params = record.to_audit_params()
    assert params["fault_type"] == "order_failed"
    assert params["fault_state"] == "degraded"
    assert params["related_object"] == {"run_id": "r1"}
    assert params["action_taken"] == "暂停新下单"
    assert "保证金不足" in params["recovery_suggestion"]
    assert "保证金不足" == params["detail"]
    assert "timestamp_ms" in params


def test_fault_record_to_alert_shape():
    record = FaultRecord(
        fault_type=FaultType.KILL_SWITCH,
        fault_state=FaultState.EXITING,
        related_object={"run_id": "killme"},
        action_taken="强制清仓停止",
    )
    alert = record.to_alert()
    assert alert["fault_type"] == "kill_switch"
    assert alert["fault_state"] == "exiting"
    assert alert["related"] == {"run_id": "killme"}
    assert "强制清仓停止" == alert["action"]


def test_fault_record_to_log_single_line():
    record = FaultRecord(
        fault_type=FaultType.GATEWAY_DISCONNECTED,
        fault_state=FaultState.RECONNECTING,
        related_object={"run_id": "r1", "node_id": "n1"},
        action_taken="进入重连等待",
        detail="TCP connection reset",
    )
    log = record.to_log()
    assert log.startswith("[gateway_disconnected]")
    assert "state=reconnecting" in log
    assert "action=进入重连等待" in log
    assert "run_id=r1" in log
    assert "detail=TCP connection reset" in log
    # 单行，无换行
    assert "\n" not in log


# ── state_transition ─────────────────────────────────────────────


def test_state_transition_fields():
    t = state_transition(
        from_state=FaultState.RUNNING,
        to_state=FaultState.DEGRADED,
        reason="网关重连超时，降级为只读",
        related_object={"run_id": "abc"},
    )
    assert isinstance(t, StateTransition)
    assert t.from_state == FaultState.RUNNING
    assert t.to_state == FaultState.DEGRADED
    assert t.reason == "网关重连超时，降级为只读"
    assert t.related_object == {"run_id": "abc"}


def test_state_transition_to_log_format():
    t = state_transition(
        from_state=FaultState.RUNNING,
        to_state=FaultState.FAILED,
        reason="进程被 kill",
    )
    log = t.to_log()
    assert log == "[transition] running → failed: 进程被 kill"


# ── gate_fault 便捷工厂 ──────────────────────────────────────────


def test_gate_fault_maps_fault_type_to_state():
    run_id = uuid.uuid4()
    f = gate_fault(
        fault_type=FaultType.GATEWAY_DISCONNECTED,
        action_taken="进入重连等待",
        run_id=run_id,
    )
    assert f.fault_state == FaultState.RECONNECTING
    assert f.related_object == {"run_id": str(run_id)}


def test_gate_fault_kill_switch_is_exiting_state():
    f = gate_fault(
        fault_type=FaultType.KILL_SWITCH,
        action_taken="强制清仓停止",
    )
    assert f.fault_state == FaultState.EXITING


def test_gate_fault_circuit_open_is_circuit_breaking():
    f = gate_fault(
        fault_type=FaultType.CIRCUIT_OPEN,
        action_taken="暂停新下单",
    )
    assert f.fault_state == FaultState.CIRCUIT_BREAKING


def test_gate_fault_recovery_override():
    f = gate_fault(
        fault_type=FaultType.ORDER_FAILED,
        action_taken="暂停新下单",
        recovery_override="手动覆盖的恢复建议",
    )
    assert f.recovery_suggestion == "手动覆盖的恢复建议"
