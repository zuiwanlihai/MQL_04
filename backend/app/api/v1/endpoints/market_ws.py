"""WS /ws/quotes — realtime quote stream (行情页实时化).

App-level route (NOT under /api/v1): the vite dev proxy only upgrades
WebSockets on the ``/ws`` prefix.  Auth uses a one-time ticket from
``POST /api/v1/market/ws-ticket`` — browsers cannot set headers on a
WebSocket handshake and a bare JWT in the URL would leak into access logs.

Frames (JSON):
  client → server: ``{"action": "subscribe"|"unsubscribe", "symbols": [...]}``
  server → client: ``{"type": "subscribed"|"unsubscribed"|"quotes"|
                     "gateway_error"|"ping"|"error", ...}``
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.services.quote_push import get_quote_hub, normalize_symbols

logger = logging.getLogger("mt5quant.quote_push")

router = APIRouter()

CLOSE_AUTH_FAILED = 4401  # custom: lets the client trigger a re-ticket flow


@router.websocket("/ws/quotes")
async def ws_quotes(
    websocket: WebSocket,
    ticket: str = Query(default="", description="一次性连接票据"),
) -> None:
    hub = get_quote_hub()
    if hub is None or not hub.consume_ticket(ticket):
        # Accept first, then close with a code — a pre-accept rejection would
        # surface to the browser as an opaque handshake failure.
        await websocket.accept()
        await websocket.close(code=CLOSE_AUTH_FAILED, reason="ticket 无效或已过期")
        return

    await websocket.accept()
    logger.info("ws quotes client connected")
    try:
        while True:
            msg = await websocket.receive_json()
            if not isinstance(msg, dict):
                await websocket.send_json({"type": "error", "detail": "帧格式错误"})
                continue
            action = msg.get("action")
            symbols = normalize_symbols(msg.get("symbols"))
            if action == "subscribe":
                if not symbols:
                    await websocket.send_json(
                        {"type": "error", "detail": "symbols 不能为空"}
                    )
                    continue
                try:
                    merged = hub.subscribe(websocket, symbols)
                except ValueError as exc:
                    await websocket.send_json({"type": "error", "detail": str(exc)})
                    continue
                await websocket.send_json(
                    {"type": "subscribed", "symbols": sorted(merged)}
                )
                snap = hub.snapshot_for(symbols)
                if snap:
                    await websocket.send_json({"type": "quotes", "items": snap})
            elif action == "unsubscribe":
                hub.unsubscribe(websocket, symbols)
                await websocket.send_json(
                    {"type": "unsubscribed", "symbols": symbols}
                )
            else:
                await websocket.send_json(
                    {"type": "error", "detail": f"未知 action：{action}"}
                )
    except WebSocketDisconnect:
        pass
    finally:
        hub.unregister(websocket)
        logger.info("ws quotes client disconnected")
