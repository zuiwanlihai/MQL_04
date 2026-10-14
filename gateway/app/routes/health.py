"""GET /health — keyless liveness/status probe (heartbeat dependency)."""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.mt5_client import client

router = APIRouter()


@router.get("/health")
async def health() -> Response:
    """Always 200 — the payload's ``status`` carries the real state."""
    payload = await client.health()
    # no_terminal 等仍返回 200（心跳据此判 degraded/offline，不判连接失败）
    from fastapi.responses import JSONResponse

    return JSONResponse(content=payload, status_code=200)
