"""FaultState — 统一的故障状态机与结构化故障输出。

优化方案 P0-4：关键故障路径的兜底和可观测性。

设计约束（见优化方案.md §三 P0-4）：
1. 不改 DB schema。FaultState 是运行时层抽象，持久化仍用现有 model
   的 status/error_code/error_message 字段；FaultRecord 走审计日志 +
   告警通道输出。避免 alembic 迁移。
2. 兼容 gateway_heartbeat 已有 online/degraded/offline，不强推替换。
3. 恢复建议不要硬编码在枚举里——用 fault_type → recovery_template
   映射表，允许各 manager 覆盖。

三层结构：
  FaultState        — 状态枚举（可观察的运行时状态）
  FaultType         — 故障分类（结构化输出的 type 字段）
  FaultRecord       — 结构化故障输出 dataclass

典型用法：
  # 在异常路径创建 FaultRecord，走审计 + 告警
  record = FaultRecord(
      fault_type=FaultType.GATEWAY_DISCONNECTED,
      fault_state=FaultState.RECONNECTING,
      related_object={"run_id": str(run.id), "node_id": str(node_id)},
      action_taken="进入重连等待",
      recovery_suggestion=FaultRecord.recovery_for(FaultType.GATEWAY_DISCONNECTED),
  )
  await record.emit(db=db)  # audit_record + alert_emit

  # 状态转换：带理由 + 关联对象
  transition = state_transition(
      from_state=FaultState.RUNNING,
      to_state=FaultState.DEGRADED,
      reason="网关重连超时，降级为只读",
      related_object={"run_id": str(run.id)},
  )
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger("mt5quant.fault_state")


# ── 状态枚举 ──────────────────────────────────────────────────────


class FaultState(str, Enum):
    """运行时可观察的故障状态。

    覆盖优化方案要求的 6 档；与 gateway_heartbeat 的 online/degraded/offline
    兼容（online=RUNNING, degraded=DEGRADED, offline=FAILED）。
    """

    RUNNING = "running"               # 正常
    RECONNECTING = "reconnecting"     # 重连中
    DEGRADED = "degraded"             # 降级（部分能力可用）
    CIRCUIT_BREAKING = "circuit_breaking"  # 熔断（暂停新下单）
    FAILED = "failed"                 # 故障
    EXITING = "exiting"               # 退出中

    # 兼容别名：gateway_heartbeat 用 status 字符串，这里允许直接比
    def matches(self, gateway_status: str) -> bool:
        mapping = {
            "online": FaultState.RUNNING,
            "starting": FaultState.RECONNECTING,
            "degraded": FaultState.DEGRADED,
            "offline": FaultState.FAILED,
        }
        return mapping.get(gateway_status) == self


# ── 故障分类 ──────────────────────────────────────────────────────


class FaultType(str, Enum):
    """结构化故障输出的 type 字段。

    覆盖优化方案要求的关键链路。每条故障路径对应一个 FaultType，
    而不是散落的 error_code 字符串。
    """

    # 网关类
    GATEWAY_DISCONNECTED = "gateway_disconnected"
    GATEWAY_RECONNECT_TIMEOUT = "gateway_reconnect_timeout"
    GATEWAY_GIVEUP = "gateway_giveup"                  # guard 放弃

    # 策略/沙箱类
    STRATEGY_CRASHED = "strategy_crashed"
    STRATEGY_TIMEOUT = "strategy_timeout"
    STRATEGY_ILLEGAL_IMPORT = "strategy_illegal_import"

    # 运行类
    STARTUP_FAILED = "startup_failed"
    BACKTEST_FAILED = "backtest_failed"
    ORDER_FAILED = "order_failed"
    POSITION_MISMATCH = "position_mismatch"
    KILL_SWITCH = "kill_switch"
    DEMOTED = "demoted"                                 # 降级为只读
    CIRCUIT_OPEN = "circuit_open"                      # 熔断触发

    # 外部依赖类
    DB_UNAVAILABLE = "db_unavailable"
    DATA_FEED_STALE = "data_feed_stale"


# ── 恢复建议映射表 ─────────────────────────────────────────────────


_RECOVERY_TEMPLATES: dict[FaultType, str] = {
    FaultType.GATEWAY_DISCONNECTED: (
        "网关连接中断。检查 MT5 终端是否在线、账户是否被锁定、"
        "网络是否可用；恢复后系统会自动重连。"
    ),
    FaultType.GATEWAY_RECONNECT_TIMEOUT: (
        "网关重连超时。MT5 终端可能卡死，需手动重启终端；"
        "重启后系统会自动恢复。"
    ),
    FaultType.GATEWAY_GIVEUP: (
        "网关 guard 放弃。MT5 终端反复重连失败，"
        "需确认账户状态、网络、终端日志。"
    ),
    FaultType.STRATEGY_CRASHED: (
        "策略执行崩溃。查看运行日志定位异常；"
        "修复策略代码后可从暂停状态恢复。"
    ),
    FaultType.STRATEGY_TIMEOUT: (
        "策略执行超时。检查策略是否有死循环或阻塞调用；"
        "必要时调高 callback_timeout_seconds。"
    ),
    FaultType.STRATEGY_ILLEGAL_IMPORT: (
        "策略尝试导入沙箱禁止的模块。"
        "修复策略代码移除非法 import。"
    ),
    FaultType.STARTUP_FAILED: (
        "启动失败。查看 error_message 定位原因；"
        "修复后重新部署。"
    ),
    FaultType.BACKTEST_FAILED: (
        "回测失败。查看失败原因（数据缺失/网关不可达/编译错误）；"
        "修复后重新启动。"
    ),
    FaultType.ORDER_FAILED: (
        "下单失败。查看 order_error_code 和 MT5 终端日志；"
        "常见原因：保证金不足、交易时间外、终端离线。"
    ),
    FaultType.POSITION_MISMATCH: (
        "持仓镜像与终端不一致。运行 reconcile 或手动核对 MT5；"
        "如果是手动平仓导致，刷新页面即可同步。"
    ),
    FaultType.KILL_SWITCH: (
        "已触发 kill switch，任务被强制停止并清仓。"
        "确认终止原因后，如需继续请重新部署。"
    ),
    FaultType.DEMOTED: (
        "任务已降级为只读状态（无法再下单）。"
        "需修复导致降级的根因后，重新部署恢复完整功能。"
    ),
    FaultType.CIRCUIT_OPEN: (
        "熔断触发，新下单被暂停。"
        "等熔断窗口结束自动恢复，或手动重置熔断器。"
    ),
    FaultType.DB_UNAVAILABLE: (
        "数据库不可用。检查 PostgreSQL 服务状态和网络连接；"
        "数据库恢复后系统自动重试。"
    ),
    FaultType.DATA_FEED_STALE: (
        "行情数据可能陈旧。检查网关连接和 MT5 终端 tick 流；"
        "恢复后自动刷新。"
    ),
}


# ── 结构化故障记录 ─────────────────────────────────────────────────


@dataclass
class FaultRecord:
    """结构化故障输出。

    每条故障都有这 5 个字段，而不是散落的 error_code + error_message。
    走审计日志 + 告警通道输出，不直接改 DB schema。

    Attributes:
        fault_type: 故障分类（FaultType 枚举）
        fault_state: 故障状态（FaultState 枚举）
        related_object: 关联对象（run_id/account_id/node_id 等）
        action_taken: 已执行的处理动作
        recovery_suggestion: 恢复建议（可覆盖默认模板）
        timestamp: 故障时间（默认 UTC now）
        detail: 额外细节（可选，如原始异常消息）
    """

    fault_type: FaultType
    fault_state: FaultState
    related_object: dict[str, Any]
    action_taken: str
    recovery_suggestion: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.recovery_suggestion:
            self.recovery_suggestion = self.default_recovery()

    def default_recovery(self) -> str:
        return _RECOVERY_TEMPLATES.get(
            self.fault_type, "查看日志定位原因；修复后重试。"
        )

    def to_audit_params(self) -> dict[str, Any]:
        """序列化为 audit_record 的 params 字段。"""
        return {
            "fault_type": self.fault_type.value,
            "fault_state": self.fault_state.value,
            "related_object": self.related_object,
            "action_taken": self.action_taken,
            "recovery_suggestion": self.recovery_suggestion,
            "timestamp_ms": int(self.timestamp.timestamp() * 1000),
            "detail": self.detail,
        }

    def to_alert(self) -> dict[str, Any]:
        """序列化为 alert_emit 的 body。"""
        return {
            "fault_type": self.fault_type.value,
            "fault_state": self.fault_state.value,
            "related": self.related_object,
            "action": self.action_taken,
            "recovery": self.recovery_suggestion,
            "detail": self.detail,
        }

    def to_log(self) -> str:
        """日志友好的单行文本。"""
        parts = [
            f"[{self.fault_type.value}]",
            f"state={self.fault_state.value}",
            f"action={self.action_taken}",
        ]
        if self.related_object:
            parts.append(
                "related=" + ",".join(f"{k}={v}" for k, v in self.related_object.items())
            )
        if self.detail:
            parts.append(f"detail={self.detail[:200]}")
        return " ".join(parts)

    async def emit(
        self,
        db: Any | None = None,
        source: str = "fault_state",
        level: str = "warn",
    ) -> None:
        """同时写审计日志 + 发告警。

        延迟 import audit/alert 模块——顶层 import 会触发 DB 连接，
        纯数据层（枚举/dataclass）不需要 DB 也能单测。

        **所有内部调用都包 try/except**：emit 是最后一道结构化输出，
        它本身绝不能因为 DB/网络故障级联杀死业务流。审计失败或告警
        失败只打 log，不抛异常。
        """
        from app.services import alert as _alert  # noqa: WPS433
        from app.services import audit as _audit  # noqa: WPS433

        if db is not None:
            try:
                await _audit.record(
                    action=f"fault_{self.fault_type.value}",
                    target_type="fault",
                    target_id=_related_object_key(self.related_object),
                    params=self.to_audit_params(),
                )
            except Exception:  # noqa: BLE001 — audit 失败不阻断
                logger.exception("fault audit emit failed: type=%s", self.fault_type.value)
        try:
            await _alert.emit(
                source=source,
                level=level,
                title=f"{self.fault_type.value} → {self.fault_state.value}",
                message=(
                    f"{self.action_taken}\n"
                    f"related={self.related_object}\n"
                    f"恢复建议：{self.recovery_suggestion}"
                    f"{chr(10) + self.detail if self.detail else ''}"
                ),
                run_id=self.related_object.get("run_id"),
                account_id=self.related_object.get("account_id"),
            )
        except Exception:  # noqa: BLE001 — alert 失败不阻断
            logger.exception("fault alert emit failed: type=%s", self.fault_type.value)


def _related_object_key(related: dict[str, Any]) -> str:
    """从 related_object 选一个最有信息量的 id 字段。"""
    for key in ("run_id", "task_id", "account_id", "node_id", "strategy_id"):
        if key in related:
            return str(related[key])
    return "_".join(f"{k}={v}" for k, v in related.items()) or "fault"


# ── 状态转换辅助 ─────────────────────────────────────────────────


@dataclass
class StateTransition:
    """带理由的状态转换记录。"""

    from_state: FaultState
    to_state: FaultState
    reason: str
    related_object: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_log(self) -> str:
        return (
            f"[transition] {self.from_state.value} → {self.to_state.value}: "
            f"{self.reason}"
        )


def state_transition(
    from_state: FaultState,
    to_state: FaultState,
    reason: str,
    related_object: dict[str, Any] | None = None,
) -> StateTransition:
    """创建状态转换记录（纯数据，不做副作用）。

    调用方可选：
    - 写 logger.info(transition.to_log())
    - 或构造 FaultRecord 走 audit + alert
    """
    return StateTransition(
        from_state=from_state,
        to_state=to_state,
        reason=reason,
        related_object=related_object or {},
    )


# ── 便捷工厂 ─────────────────────────────────────────────────────


def gate_fault(
    fault_type: FaultType,
    action_taken: str,
    run_id: uuid.UUID | str | None = None,
    account_id: uuid.UUID | str | None = None,
    node_id: uuid.UUID | str | None = None,
    detail: str = "",
    recovery_override: str | None = None,
) -> FaultRecord:
    """快速构造网关/运行类故障记录。"""
    state_map = {
        FaultType.GATEWAY_DISCONNECTED: FaultState.RECONNECTING,
        FaultType.GATEWAY_RECONNECT_TIMEOUT: FaultState.RECONNECTING,
        FaultType.GATEWAY_GIVEUP: FaultState.FAILED,
        FaultType.STRATEGY_CRASHED: FaultState.FAILED,
        FaultType.STRATEGY_TIMEOUT: FaultState.DEGRADED,
        FaultType.STRATEGY_ILLEGAL_IMPORT: FaultState.FAILED,
        FaultType.STARTUP_FAILED: FaultState.FAILED,
        FaultType.BACKTEST_FAILED: FaultState.FAILED,
        FaultType.ORDER_FAILED: FaultState.DEGRADED,
        FaultType.POSITION_MISMATCH: FaultState.DEGRADED,
        FaultType.KILL_SWITCH: FaultState.EXITING,
        FaultType.DEMOTED: FaultState.DEGRADED,
        FaultType.CIRCUIT_OPEN: FaultState.CIRCUIT_BREAKING,
    }
    related: dict[str, Any] = {}
    if run_id:
        related["run_id"] = str(run_id)
    if account_id:
        related["account_id"] = str(account_id)
    if node_id:
        related["node_id"] = str(node_id)
    return FaultRecord(
        fault_type=fault_type,
        fault_state=state_map.get(fault_type, FaultState.FAILED),
        related_object=related,
        action_taken=action_taken,
        recovery_suggestion=recovery_override or "",
        detail=detail,
    )
