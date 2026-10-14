"""POST /trade/order · /trade/close · /trade/cancel · GET /trade/positions|orders|deals.

Live execution surface (M6).  Every command runs on the single MT5 worker via
``client.run``; trade commands use ``PRIORITY_TRADE`` so a Kill Switch is never
stuck behind a queued market poll.  The server-side gate (feature flag + login
whitelist + algo-trade switches) lives in ``MT5Client._check_trade_allowed_sync``.

P1-A1 idempotency:
  All four write routes (order/pending/close/cancel) share a process-local
  ``_IDEMPOTENCY_CACHE``.  Keys are derived from the inbound payload's
  ``comment`` field — the live manager in the backend already stamps every
  order with ``run_id[:8]:req_id[:8]``.  Repeating the exact same payload within
  ``settings.IDEMPOTENCY_TTL_SECONDS`` returns the cached result with header
  ``X-Idempotent-Replay: true`` without talking to MT5 again.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from functools import wraps
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from app.config import add_trade_allowed_login, remove_trade_allowed_login, settings
from app.mt5_client import client
from app.schemas import (
    CancelRequest,
    CloseRequest,
    OrderRequest,
    PendingRequest,
    TradeResult,
    WhitelistIn,
    WhitelistOut,
)
from app.security import require_gateway_key

router = APIRouter(prefix="/trade", dependencies=[Depends(require_gateway_key)])


# ── P1-A1 idempotency cache ──────────────────────────────────────────

_IDEMPOTENCY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
# MT5 SDK silently truncates the comment at 31 chars — the backend writes
# "run_id[:8]:req_id[:8]" which is exactly 17 chars, fits safely.
_COMMENT_KEY_RE = re.compile(r"^([0-9a-f]{8}:[0-9a-f]{4,16})")


def _idempotency_key(op: str, comment: str, payload: dict[str, Any]) -> str:
    """Derive a stable idempotency key from the trade payload.

    Priority order:
      1. ``run_id:req_id`` stamped by backend live-manager in the comment.
      2. Fallback: ``op:hash(json_payload)`` — deterministic but the backend
         SHOULD always provide case (1).
    """
    m = _COMMENT_KEY_RE.match((comment or "").strip())
    if m:
        # include op so close/order of the same run:req don't collide
        return f"{op}:{m.group(1)}"
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()[:16]
    return f"{op}:{digest}"


def _prune_expired() -> None:
    """Drop cache entries older than TTL (best-effort, called on each write)."""
    now = time.time()
    dead = [k for k, (expires, _) in _IDEMPOTENCY_CACHE.items() if expires < now]
    for k in dead:
        _IDEMPOTENCY_CACHE.pop(k, None)


def _idempotent(op_label: str):
    """Decorator that makes a trade route process-local idempotent.

    The wrapped coroutine must accept the FastAPI ``payload`` as its first
    argument (the Pydantic request body model).  Successful results are stored
    in ``_IDEMPOTENCY_CACHE`` keyed by the combined op + request identity;
    replays receive the cached dict with header ``X-Idempotent-Replay: true``.
    Errors (HTTPException from downstream) are NOT cached — we want retries
    to hit MT5 again so the caller can see updated broker state.
    """

    def decorator(fn):
        @wraps(fn)
        async def wrapper(payload, *args, **kwargs):
            payload_dict = payload.model_dump()
            key = _idempotency_key(op_label, payload_dict.get("comment", ""), payload_dict)
            _prune_expired()
            hit = _IDEMPOTENCY_CACHE.get(key)
            if hit is not None:
                _, cached = hit
                return JSONResponse(
                    content=cached,
                    headers={"X-Idempotent-Replay": "true"},
                )
            result = await fn(payload, *args, **kwargs)
            # Cache only successful results (retcode 10009, or any success=True
            # payload from mt5_client).  Errors bubble up so callers can retry
            # against MT5 directly.
            if isinstance(result, dict) and result.get("success"):
                expires = time.time() + settings.IDEMPOTENCY_TTL_SECONDS
                _IDEMPOTENCY_CACHE[key] = (expires, result)
            return result

        return wrapper

    return decorator


# ── whitelist ──────────────────────────────────────────────────────────

@router.get("/whitelist", response_model=WhitelistOut)
async def whitelist_get() -> dict:
    """Current trade-whitelist state (feature flag + allowed logins)."""
    return {
        "trade_enabled": settings.TRADE_ENABLED,
        "logins": sorted(settings.trade_allowed_logins),
    }


@router.post("/whitelist", response_model=WhitelistOut)
async def whitelist_add(payload: WhitelistIn) -> dict:
    """Add an MT5 login to the trade whitelist (runtime + persisted to .env)."""
    if not settings.TRADE_ENABLED:
        raise HTTPException(status_code=409, detail="网关交易开关未开启（TRADE_ENABLED=false）")
    logins = add_trade_allowed_login(payload.login)
    return {"trade_enabled": True, "logins": sorted(logins)}


@router.delete("/whitelist/{login}", response_model=WhitelistOut)
async def whitelist_remove(login: int) -> dict:
    """Remove an MT5 login from the trade whitelist (runtime + persisted)."""
    logins = remove_trade_allowed_login(login)
    return {"trade_enabled": settings.TRADE_ENABLED, "logins": sorted(logins)}


# ── trade commands (P1-A1: all idempotent) ────────────────────────────

@router.post("/order", response_model=TradeResult)
@_idempotent("order")
async def order(payload: OrderRequest) -> dict:
    """Market buy/sell on the bound account. 409 when trading is disallowed."""
    return await client.place_market(
        payload.symbol,
        payload.volume,
        payload.side,
        magic=payload.magic,
        comment=payload.comment,
        deviation=payload.deviation,
    )


@router.post("/pending", response_model=TradeResult)
@_idempotent("pending")
async def pending(payload: PendingRequest) -> dict:
    """Place a limit/stop pending order. 409 when trading is disallowed."""
    return await client.place_pending(
        payload.symbol,
        payload.volume,
        payload.order_type,
        payload.price,
        sl=payload.sl,
        tp=payload.tp,
        magic=payload.magic,
        comment=payload.comment,
    )


@router.post("/close", response_model=TradeResult)
@_idempotent("close")
async def close(payload: CloseRequest) -> dict:
    """Close (part of) an open position by ticket; volume=None closes all."""
    return await client.close_position(
        payload.ticket, payload.volume, magic=payload.magic,
        deviation=payload.deviation,
    )


@router.post("/cancel", response_model=TradeResult)
@_idempotent("cancel")
async def cancel(payload: CancelRequest) -> dict:
    """Cancel a pending order by ticket."""
    return await client.cancel_order(payload.ticket)


# ── reads ─────────────────────────────────────────────────────────────

@router.get("/positions")
async def positions(
    symbol: Annotated[str | None, Query(description="按品种过滤，留空=全部")] = None,
) -> list[dict]:
    """Open positions on the account (live mirror for reconciliation)."""
    return await client.positions(symbol)


@router.get("/orders")
async def orders(
    symbol: Annotated[str | None, Query(description="按品种过滤，留空=全部")] = None,
) -> list[dict]:
    """Active (pending) orders."""
    return await client.orders(symbol)


@router.get("/deals")
async def deals(
    start: Annotated[int, Query(ge=0, description="起始时间（epoch 秒，含）")],
    end: Annotated[int | None, Query(ge=0, description="结束时间（epoch 秒，含）")] = None,
    symbol: Annotated[str | None, Query(description="按品种过滤")] = None,
    position: Annotated[
        int | None, Query(ge=1, description="按持仓票号查成交（历史未同步时可靠）")
    ] = None,
) -> list[dict]:
    """Executed deals in [start, end] (or all deals of one position); end defaults to now."""
    return await client.deals(start, end if end is not None else int(time.time()), symbol,
                              position=position)
