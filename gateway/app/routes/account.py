"""POST /account/login · /account/logout · GET /account — MT5 session + snapshot."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.mt5_client import client
from app.schemas import AccountSummary, LoginRequest
from app.security import require_gateway_key

router = APIRouter(prefix="/account", dependencies=[Depends(require_gateway_key)])


@router.get("", response_model=AccountSummary)
async def account() -> dict:
    """Real-time account snapshot (balance/equity/margin) for the live mirror."""
    summary = await client.account()
    if summary is None:
        raise HTTPException(status_code=409, detail="MT5 未登录，无账户快照")
    return summary


@router.post("/login", response_model=AccountSummary)
async def login(payload: LoginRequest) -> dict:
    """Authenticate against MT5; 400 with a Chinese message on rejection."""
    return await client.login(payload.login, payload.password, payload.server)


@router.post("/logout")
async def logout() -> dict:
    """Idempotent — succeeds even when no session is active."""
    await client.logout()
    return {"status": "ok"}
