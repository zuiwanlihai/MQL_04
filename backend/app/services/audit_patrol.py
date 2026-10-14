"""Audit-chain scheduled patrol (PRD M7 审计链定时巡检).

The hash chain protects audit rows from silent tampering, but until now
verification only happened when an admin hit ``POST /audit/verify`` — a
broken link could sit unnoticed for weeks.  This loop walks the chain on a
fixed cadence and emits ``audit_chain`` alerts, so M10 channel fan-out
applies automatically.

Behavior per cycle:
  * ``verify_chain()`` walk; result audited as an ``audit_patrol`` row
    (evidence the patrol ran, success or failure).
  * alert on **verdict change**: ok→fork / ok→critical / fork→critical …
    while the verdict holds, only log (no re-alert spam every cycle).
  * fork and critical are separate states on purpose: 分叉 is a historical
    concurrency artifact with intact content, 篡改 is content that moved.
    Folding both into "broken" would pin the patrol in that state forever
    once a benign fork exists — and then a *real* tamper changes nothing,
    so it never alerts.  A fork must not become the next tamper's cover.
  * back to ok → info alert so the incident can be closed.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.services import audit as audit_svc
from app.services.alert import emit as alert_emit

logger = logging.getLogger("mt5quant.audit_patrol")

# None = first run; "ok" / "fork" / "critical" = previous patrol verdict
_last_verdict: str | None = None


async def patrol_once() -> tuple[int, str, bool]:
    """One patrol pass; returns (total, verdict, fired_alert).

    ``verdict`` 是 ``ok`` | ``fork`` | ``critical``（见 ``audit.verdict``）。
    """
    global _last_verdict

    total, findings = await audit_svc.verify_chain()
    kind = audit_svc.verdict(findings)
    first = findings[0] if findings else None
    await audit_svc.record(
        "audit_patrol",
        actor=audit_svc.AuditActor(actor_type="system", actor_id=None,
                                   actor_label="audit-patrol", source_ip=""),
        target_type="system", target_id="audit_chain",
        params={"total": total, "ok": kind == "ok", "verdict": kind,
                "broken_index": first.index if first else -1,
                "findings_total": len(findings)},
        result="success" if kind == "ok" else "failure",
    )

    # 只在口径**变化**时告警；首次巡检就直接完好不播报（系统刚起来不是新闻）。
    fired = False
    if kind != _last_verdict and not (_last_verdict is None and kind == "ok"):
        fired = True
        if kind == "critical":
            # kind != "ok" ⇒ findings 非空 ⇒ first 必定存在（verdict() 从 findings 算）
            assert first is not None
            await alert_emit(
                source="audit_chain", level="critical",
                title=(f"审计链巡检发现异常（{len(findings)} 处，"
                       f"首个在第 {first.index + 1} 条）"),
                message=(f"定时巡检：共 {total} 条审计记录，{len(findings)} 处异常；"
                         f"第一处在索引 {first.index}（0 基）：{first.detail}。"
                         "可能存在篡改或删除，请立即排查。状态不变期间后续巡检"
                         "只记日志，修复后会发送恢复通知。"),
            )
        elif kind == "fork":
            assert first is not None
            await alert_emit(
                source="audit_chain", level="warn",
                title=f"审计链巡检发现分叉（{len(findings)} 处，非篡改）",
                message=(f"定时巡检：共 {total} 条审计记录，{len(findings)} 处分叉；"
                         f"第一处在索引 {first.index}（0 基）：{first.detail}。"
                         "分叉行自身内容完好，是并发写入的旧缺陷留下的痕迹；"
                         "请核对时间戳确认是历史遗留。此后若出现内容被改/被删的"
                         "异常，会另发严重告警。"),
            )
        else:  # kind == "ok"，且此前不是完好 → 恢复
            await alert_emit(
                source="audit_chain", level="info",
                title="审计链巡检恢复",
                message=f"定时巡检通过：共 {total} 条审计记录，哈希链完整"
                        "（此前曾检出异常，若非本人修复请排查）。",
            )
    _last_verdict = kind
    if kind != "ok":
        logger.warning("audit-chain patrol: verdict=%s findings=%s (total=%s)",
                       kind, len(findings), total)
    return total, kind, fired


def reset_state() -> None:
    """Clear the previous-verdict memory (tests / hot reload)."""
    global _last_verdict
    _last_verdict = None


async def patrol_loop() -> None:
    """Forever loop — started from the FastAPI lifespan."""
    while True:
        try:
            await patrol_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never let the loop die
            logger.exception("audit-chain patrol iteration failed")
        await asyncio.sleep(settings.AUDIT_PATROL_INTERVAL_SECONDS)
