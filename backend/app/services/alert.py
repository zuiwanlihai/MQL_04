"""Alert dispatch (M6 + M10): in-site row + fire-and-forget channels.

The in-site row is written synchronously (callers on the kill-switch /
reconcile path await it).  Out-of-band channels (email / Telegram / DingTalk
/ WeCom, M10) are dispatched by a detached task — a hung SMTP relay can
never stall alerting itself.
"""

from __future__ import annotations

import logging
import uuid

from app.engine.session import AsyncSessionLocal
from app.models import AlertEvent
from app.services.notify.dispatcher import schedule_dispatch

logger = logging.getLogger("mt5quant.alert")


async def emit(
    source: str,
    title: str,
    message: str = "",
    *,
    level: str = "warn",
    account_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
) -> AlertEvent:
    """Persist an in-site alert, then fan out to notification channels."""
    async with AsyncSessionLocal() as db:
        row = AlertEvent(
            level=level, source=source, title=title[:200], message=message,
            account_id=account_id, run_id=run_id,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    logger.warning("ALERT[%s/%s] %s — %s", level, source, title, message[:200])
    schedule_dispatch(row.id)
    return row
