"""Alert dispatcher (M10): fan out one in-site alert to notification channels.

``schedule_dispatch`` is the ONLY hook ``services.alert.emit`` calls — it
spawns a detached task so network sends (SMTP relays, Telegram…) can never
block the kill-switch / reconcile paths that await emit().  Every failure is
logged and swallowed; a dead channel must not degrade alerting itself.

Alerts are platform-level (no per-user ownership — same semantics as the
M7 alert center), so dispatch targets every user's enabled channels whose
``min_level`` admits the alert.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.engine.session import AsyncSessionLocal
from app.models import AlertEvent, NotificationChannel
from app.services.notify.email import send_email
from app.services.notify.webhook import SENDERS

logger = logging.getLogger("mt5quant.notify")

LEVEL_RANK = {"info": 0, "warn": 1, "error": 2, "critical": 3}


def level_admits(min_level: str, level: str) -> bool:
    try:
        return LEVEL_RANK[level] >= LEVEL_RANK[min_level]
    except KeyError:
        return False


async def _deliver(alert_id: uuid.UUID) -> None:
    """Load the alert row, fan out to admitting channels, mark emailed_at."""
    try:
        async with AsyncSessionLocal() as db:
            alert = await db.get(AlertEvent, alert_id)
            if alert is None:
                return
            channels = (await db.execute(
                select(NotificationChannel).where(
                    NotificationChannel.enabled.is_(True))
            )).scalars().all()
    except Exception:
        logger.exception("notify: failed to load alert %s", alert_id)
        return

    email_sent = False
    for ch in channels:
        if not level_admits(ch.min_level, alert.level):
            continue
        try:
            if ch.kind == "email":
                ok, detail = await send_email(
                    ch.target, f"[MT5量化 {alert.level}] {alert.title}",
                    alert.message or alert.title)
                if ok:
                    email_sent = True
            elif ch.kind in SENDERS:
                ok, detail = await SENDERS[ch.kind](
                    ch.target, alert.title, alert.message or alert.title, alert.level)
            else:
                continue
            if ok:
                logger.info("notify[%s→%s] ok", ch.kind, _mask(ch.target))
            else:
                logger.warning("notify[%s→%s] failed: %s", ch.kind, _mask(ch.target), detail)
        except Exception:
            logger.exception("notify[%s] crashed (swallowed)", ch.kind)

    if email_sent:
        try:
            async with AsyncSessionLocal() as db:
                alert = await db.get(AlertEvent, alert_id)
                if alert is not None:
                    alert.emailed_at = datetime.now(UTC)
                    db.add(alert)
                    await db.commit()
        except Exception:
            logger.exception("notify: failed to stamp emailed_at")


def _mask(target: str) -> str:
    if "@" in target and ":" not in target.split("@")[0]:
        name, _, domain = target.partition("@")
        return f"{name[:2]}***@{domain}"
    return target[:6] + "***"


def schedule_dispatch(alert_id: uuid.UUID) -> None:
    """Fire-and-forget: never awaits, never raises into emit()."""
    try:
        asyncio.get_running_loop().create_task(
            _deliver(alert_id), name=f"notify-{str(alert_id)[:8]}")
    except RuntimeError:  # no running loop (sync context / teardown)
        logger.warning("notify: no event loop, dispatch skipped for %s", alert_id)
