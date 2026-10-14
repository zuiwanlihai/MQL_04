"""Audit-chain scheduled patrol (PRD M7 审计链定时巡检).

The hash chain protects audit rows from silent tampering, but until now
verification only happened when an admin hit ``POST /audit/verify`` — a
broken link could sit unnoticed for weeks.  This loop walks the chain on a
fixed cadence and, on break, emits the same critical ``audit_chain`` alert
as the manual endpoint, so M10 channel fan-out applies automatically.

Behavior per cycle:
  * ``verify_chain()`` walk; result audited as an ``audit_patrol`` row
    (evidence the patrol ran, success or failure).
  * broken: critical alert on the intact→broken transition; while still
    broken only log (avoid re-alert spam every cycle).  Recovery emits an
    info alert so the incident can be closed.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.services import audit as audit_svc
from app.services.alert import emit as alert_emit

logger = logging.getLogger("mt5quant.audit_patrol")

# None = first run; True/False = previous patrol verdict
_last_ok: bool | None = None


async def patrol_once() -> tuple[int, int, bool]:
    """One patrol pass; returns (total, broken_index, fired_alert)."""
    global _last_ok

    total, broken = await audit_svc.verify_chain()
    ok = broken == -1
    await audit_svc.record(
        "audit_patrol",
        actor=audit_svc.AuditActor(actor_type="system", actor_id=None,
                                   actor_label="audit-patrol", source_ip=""),
        target_type="system", target_id="audit_chain",
        params={"total": total, "ok": ok, "broken_index": broken},
        result="success" if ok else "failure",
    )

    fired = False
    if ok:
        if _last_ok is False:  # incident closed (e.g. repaired out-of-band)
            await alert_emit(
                source="audit_chain", level="info",
                title="审计链巡检恢复",
                message=f"定时巡检通过：共 {total} 条审计记录，哈希链完整"
                        "（此前曾检出断链，若非本人修复请排查）。",
            )
            fired = True
    elif _last_ok is not False:  # intact→broken transition → alert once
        await alert_emit(
            source="audit_chain", level="critical",
            title=f"审计链巡检发现断链（第 {broken + 1} 条）",
            message=(f"定时巡检：共 {total} 条审计记录，链在索引 {broken}（0 基）"
                     "处断裂，可能存在篡改或删除，请立即排查。后续巡检将只记日志"
                     "不再重复告警，修复后会发送恢复通知。"),
        )
        fired = True
    _last_ok = ok
    if not ok:
        logger.warning("audit-chain patrol: broken at index %s (total=%s)",
                       broken, total)
    return total, broken, fired


def reset_state() -> None:
    """Clear the previous-verdict memory (tests / hot reload)."""
    global _last_ok
    _last_ok = None


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
