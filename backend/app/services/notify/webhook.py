"""Webhook alert senders (M10): Telegram / DingTalk / WeCom.

Each provider has its own payload shape.  All calls go through ``httpx`` with
a bounded timeout and return ``(ok, detail)`` — failures are reported, never
raised, so the dispatcher can fire-and-forget safely.

``target`` formats:
- telegram: ``"bot_token:chat_id"``
- dingtalk / wecom: full webhook URL (secret query string included)
"""

from __future__ import annotations

import httpx

from app.config import settings

LEVEL_EMOJI = {"info": "ℹ️", "warn": "⚠️", "error": "🟠", "critical": "🚨"}


def _timeout() -> float:
    return settings.NOTIFY_TIMEOUT_SECONDS


async def send_telegram(target: str, title: str, message: str, level: str) -> tuple[bool, str]:
    if ":" not in target:
        return False, "Telegram 目标格式应为 bot_token:chat_id"
    token, _, chat_id = target.partition(":")
    if not token or not chat_id:
        return False, "Telegram bot_token / chat_id 不能为空"
    text = f"{LEVEL_EMOJI.get(level, '🔔')} {title}\n{message}"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(url, json={"chat_id": chat_id, "text": text})
    except httpx.HTTPError as exc:
        return False, f"Telegram 请求失败：{type(exc).__name__}"
    if resp.status_code >= 400 or not resp.json().get("ok", False):
        return False, f"Telegram 返回异常：HTTP {resp.status_code}"
    return True, "已发送"


async def _send_json_post(target: str, payload: dict, name: str) -> tuple[bool, str]:
    if not target.startswith("http"):
        return False, f"{name} 目标必须是 http(s) webhook URL"
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(target, json=payload)
    except httpx.HTTPError as exc:
        return False, f"{name} 请求失败：{type(exc).__name__}"
    if resp.status_code >= 400:
        return False, f"{name} 返回 HTTP {resp.status_code}"
    # DingTalk/WeCom return 200 with an errcode in the body.
    try:
        errcode = resp.json().get("errcode", 0)
    except Exception:
        errcode = 0
    if errcode not in (0, None):
        return False, f"{name} 返回错误码 {errcode}"
    return True, "已发送"


async def send_dingtalk(target: str, title: str, message: str, level: str) -> tuple[bool, str]:
    payload = {"msgtype": "markdown",
               "markdown": {"title": title, "text": f"### {title}\n\n{message}"}}
    return await _send_json_post(target, payload, "钉钉")


async def send_wecom(target: str, title: str, message: str, level: str) -> tuple[bool, str]:
    payload = {"msgtype": "markdown",
               "markdown": {"content": f"**{title}**\n{message}"}}
    return await _send_json_post(target, payload, "企业微信")


SENDERS = {
    "telegram": send_telegram,
    "dingtalk": send_dingtalk,
    "wecom": send_wecom,
}
