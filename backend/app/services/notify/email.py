"""Email alert sender (M10).

Uses stdlib ``smtplib`` on a worker thread (``asyncio.to_thread``) so a slow
or hung SMTP relay can never block the event loop — and therefore never
blocks ``services.alert.emit``, which the kill-switch / reconcile paths await.

Returns ``(ok, detail)`` instead of raising: the dispatcher logs failures but
must not propagate them into the alerting flow.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from app.config import settings


def _smtp_configured() -> bool:
    return bool(settings.SMTP_HOST and (settings.SMTP_FROM or settings.SMTP_USER))


def _send_sync(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    timeout = settings.NOTIFY_TIMEOUT_SECONDS
    if settings.SMTP_TLS:
        server: smtplib.SMTP = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT,
                                               timeout=timeout)
    else:
        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=timeout)
    try:
        if not settings.SMTP_TLS:
            server.starttls()
        if settings.SMTP_USER:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


async def send_email(to: str, subject: str, body: str) -> tuple[bool, str]:
    """Best-effort email send; never raises."""
    if not _smtp_configured():
        return False, "SMTP 未配置（SMTP_HOST/SMTP_FROM），邮件渠道不可用"
    try:
        await asyncio.to_thread(_send_sync, to, subject, body)
        return True, "已发送"
    except (smtplib.SMTPException, OSError) as exc:
        return False, f"邮件发送失败：{type(exc).__name__}: {str(exc)[:180]}"
    except Exception as exc:  # defensive — a send must never crash the caller
        return False, f"邮件发送异常：{type(exc).__name__}"
