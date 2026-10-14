"""Shared-secret header check (``X-Gateway-Key``).

``/health`` is intentionally exempt — the backend heartbeat must be able to
read it even if the key drifts, and it exposes nothing sensitive.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from app.config import settings


async def require_gateway_key(
    x_gateway_key: str | None = Header(default=None, alias="X-Gateway-Key"),
) -> None:
    """Reject requests carrying a missing or wrong gateway key (401)."""
    if x_gateway_key is None or not secrets.compare_digest(
        x_gateway_key, settings.GATEWAY_API_KEY
    ):
        raise HTTPException(status_code=401, detail="网关密钥无效")
