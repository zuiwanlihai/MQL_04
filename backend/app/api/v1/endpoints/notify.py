"""Notification channel CRUD + test endpoints (M10).

Per-user subscription management for out-of-band alert channels.  ``target``
secrets are masked in every response and validated on write.  All mutations
are audited (PRD M7 ①「配置变更 / 权限开关变更」).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.dependencies import CurrentUser, DBSession
from app.models import NotificationChannel
from app.schemas.notify import (
    ChannelCreate,
    ChannelKind,
    ChannelOut,
    ChannelTestOut,
    ChannelUpdate,
)
from app.services.audit import AuditActor
from app.services.audit import record as audit_record
from app.services.notify.email import send_email
from app.services.notify.webhook import SENDERS

router = APIRouter()


def _user_id(current: dict) -> uuid.UUID:
    return uuid.UUID(current["sub"])


def _actor(current: dict, request: Request | None = None) -> AuditActor:
    ip = request.client.host if request is not None and request.client else ""
    return AuditActor(actor_type="user", actor_id=_user_id(current),
                      actor_label=str(current.get("email") or current["sub"])[:120],
                      source_ip=ip or "")


def mask_target(kind: str, target: str) -> str:
    """Redact secrets — webhook URL query strings, bot tokens, email local-part."""
    if kind == "email":
        name, _, domain = target.partition("@")
        return f"{name[:2]}***@{domain}" if domain else "***"
    if kind == "telegram":
        token, _, chat = target.partition(":")
        return f"***:{chat[-4:]}" if chat else "***"
    # webhook URLs: keep scheme+host, mask the secret path/query
    return target.split("?")[0][:48] + "***"


def _validate_target(kind: str, target: str) -> None:
    if kind == "email" and "@" not in target:
        raise HTTPException(status_code=422, detail="邮箱地址格式不正确")
    if kind == "telegram" and ":" not in target:
        raise HTTPException(status_code=422, detail="Telegram 目标格式应为 bot_token:chat_id")
    if kind in ("dingtalk", "wecom") and not target.startswith("http"):
        raise HTTPException(status_code=422, detail="Webhook 目标必须是 http(s) URL")


async def _get_owned_channel(db, channel_id: uuid.UUID, current: dict) -> NotificationChannel:
    ch = await db.get(NotificationChannel, channel_id)
    if ch is None or str(ch.user_id) != current["sub"]:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    return ch


def _out(ch: NotificationChannel) -> ChannelOut:
    return ChannelOut(
        id=ch.id, kind=cast(ChannelKind, ch.kind), target_masked=mask_target(ch.kind, ch.target),
        min_level=ch.min_level, enabled=ch.enabled, verified_at=ch.verified_at,
        created_at=ch.created_at,
    )


@router.get("/channels", response_model=list[ChannelOut])
async def list_channels(current: CurrentUser, db: DBSession) -> list[ChannelOut]:
    rows = (await db.execute(
        select(NotificationChannel).where(NotificationChannel.user_id == _user_id(current))
        .order_by(NotificationChannel.created_at)
    )).scalars().all()
    return [_out(r) for r in rows]


@router.post("/channels", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
async def create_channel(
    payload: ChannelCreate, current: CurrentUser, db: DBSession, request: Request
) -> ChannelOut:
    _validate_target(payload.kind, payload.target)
    ch = NotificationChannel(
        user_id=_user_id(current), kind=payload.kind, target=payload.target,
        min_level=payload.min_level, enabled=True,
    )
    db.add(ch)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="该通知渠道已存在") from None
    await db.refresh(ch)
    await audit_record("channel_change", actor=_actor(current, request),
                       target_type="notification_channel", target_id=str(ch.id),
                       params={"op": "create", "kind": ch.kind,
                               "target": mask_target(ch.kind, ch.target),
                               "min_level": ch.min_level})
    return _out(ch)


@router.patch("/channels/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: uuid.UUID, payload: ChannelUpdate,
    current: CurrentUser, db: DBSession, request: Request
) -> ChannelOut:
    ch = await _get_owned_channel(db, channel_id, current)
    old = {"min_level": ch.min_level, "enabled": ch.enabled}
    if payload.min_level is not None:
        ch.min_level = payload.min_level
    if payload.enabled is not None:
        ch.enabled = payload.enabled
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    await audit_record("channel_change", actor=_actor(current, request),
                       target_type="notification_channel", target_id=str(ch.id),
                       params={"op": "update", "old": old,
                               "new": {"min_level": ch.min_level, "enabled": ch.enabled}})
    return _out(ch)


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: uuid.UUID, current: CurrentUser, db: DBSession, request: Request
) -> None:
    ch = await _get_owned_channel(db, channel_id, current)
    kind = ch.kind
    await db.delete(ch)
    await db.commit()
    await audit_record("channel_change", actor=_actor(current, request),
                       target_type="notification_channel", target_id=str(channel_id),
                       params={"op": "delete", "kind": kind})


@router.post("/channels/{channel_id}/test", response_model=ChannelTestOut)
async def test_channel(
    channel_id: uuid.UUID, current: CurrentUser, db: DBSession, request: Request
) -> ChannelTestOut:
    ch = await _get_owned_channel(db, channel_id, current)
    title = "测试通知渠道"
    message = "这是一条来自 MT5 量化交易平台的渠道测试消息，收到即表示配置正确。"

    if ch.kind == "email":
        ok, detail = await send_email(ch.target, f"[MT5量化 测试] {title}", message)
        if ok:
            ch.verified_at = datetime.now(UTC)
            db.add(ch)
            await db.commit()
    elif ch.kind in SENDERS:
        ok, detail = await SENDERS[ch.kind](ch.target, title, message, "info")
    else:
        ok, detail = False, "未知渠道类型"

    await audit_record("channel_test", actor=_actor(current, request),
                       target_type="notification_channel", target_id=str(ch.id),
                       params={"kind": ch.kind, "ok": ok},
                       result="success" if ok else "failure",
                       error_code=None if ok else "SEND_FAILED")
    return ChannelTestOut(ok=ok, detail=detail)
