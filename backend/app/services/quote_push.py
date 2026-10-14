"""Quote push hub — WS fan-out for the market page (行情页实时化).

A lazy background pump polls the gateway batch-quotes endpoint at
``MARKET_PUSH_POLL_SECONDS`` while at least one WS client is subscribed;
per-symbol snapshots are diffed on (bid, ask, last, time) and only changed
symbols are fanned out — market closure therefore stays naturally silent.
With zero subscribers the pump never touches the gateway.  Gateway failures
degrade to a ``gateway_error`` frame with exponential backoff (1s→10s);
connected clients are never dropped.

WS auth uses one-time short-TTL tickets issued by ``POST /market/ws-ticket``
so JWTs never appear in URLs / access logs (browsers cannot set headers on
WebSocket connections).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time

from fastapi import WebSocket

from app.config import settings
from app.services.gateway_client import GatewayClient, GatewayError
from app.services.market_mapper import map_quote

logger = logging.getLogger("mt5quant.quote_push")

TICKET_TTL_SECONDS = 30
PING_INTERVAL_SECONDS = 15.0
ERROR_BACKOFF_MAX_SECONDS = 10.0


def normalize_symbols(raw: object) -> list[str]:
    """Uppercase / strip / dedupe; preserves order, drops empties."""
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        name = str(item or "").strip().upper()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


class QuoteHub:
    """Subscription registry + change-diffing broadcaster + poll pump."""

    def __init__(self, gateway: GatewayClient | None = None) -> None:
        self.gateway = gateway or GatewayClient()
        # conn → its subscribed symbols (mirrored by _symbol_conns).
        self._conn_symbols: dict[WebSocket, set[str]] = {}
        self._symbol_conns: dict[str, set[WebSocket]] = {}
        # symbol_id → last pushed quote dump (diff baseline / subscribe prime).
        self._snapshot: dict[str, dict] = {}
        # one-time WS tickets: ticket → expire epoch.
        self._tickets: dict[str, float] = {}
        # Grows on gateway failures (2x, capped), resets on success.
        self.error_backoff = 1.0

    # -- tickets -------------------------------------------------------------
    def issue_ticket(self) -> str:
        now = time.time()
        for t in [t for t, exp in self._tickets.items() if exp <= now]:
            self._tickets.pop(t, None)
        ticket = secrets.token_urlsafe(32)
        self._tickets[ticket] = now + TICKET_TTL_SECONDS
        return ticket

    def consume_ticket(self, ticket: str) -> bool:
        """Single-use: pop the ticket; valid only while unexpired."""
        expiry = self._tickets.pop(ticket or "", 0.0)
        return expiry > time.time()

    # -- registry ------------------------------------------------------------
    def subscribed_symbols(self) -> list[str]:
        """Global union across all connections (the pump polls exactly this)."""
        return sorted(self._symbol_conns)

    def conn_symbols(self, ws: WebSocket) -> set[str]:
        return set(self._conn_symbols.get(ws, set()))

    def snapshot_for(self, symbols: list[str]) -> list[dict]:
        """Latest known dumps for a freshly subscribed connection."""
        return [self._snapshot[s] for s in symbols if s in self._snapshot]

    def subscribe(self, ws: WebSocket, symbols: list[str]) -> set[str]:
        """Add symbols to a connection; returns its full merged set.

        Raises ``ValueError`` when the per-connection limit would be exceeded
        (the whole frame is rejected — nothing is registered).
        """
        current = self._conn_symbols.setdefault(ws, set())
        merged = current | set(symbols)
        if len(merged) > settings.MARKET_PUSH_MAX_SYMBOLS:
            raise ValueError(
                f"订阅品种数超限：最多 {settings.MARKET_PUSH_MAX_SYMBOLS} 个"
            )
        self._conn_symbols[ws] = merged
        for s in merged - current:
            self._symbol_conns.setdefault(s, set()).add(ws)
        return merged

    def unsubscribe(self, ws: WebSocket, symbols: list[str]) -> None:
        current = self._conn_symbols.get(ws)
        if not current:
            return
        for s in symbols:
            current.discard(s)
            conns = self._symbol_conns.get(s)
            if conns is not None:
                conns.discard(ws)
                if not conns:
                    self._symbol_conns.pop(s, None)

    def unregister(self, ws: WebSocket) -> None:
        for s in self._conn_symbols.pop(ws, set()):
            conns = self._symbol_conns.get(s)
            if conns is not None:
                conns.discard(ws)
                if not conns:
                    self._symbol_conns.pop(s, None)

    # -- diff + fan-out --------------------------------------------------------
    def detect_changed(self, dumps: list[dict]) -> list[dict]:
        """Return dumps unseen so far or with a changed quote fingerprint."""
        changed: list[dict] = []
        for d in dumps:
            sid = str(d.get("symbol_id") or "")
            prev = self._snapshot.get(sid)
            if (
                prev is None
                or prev.get("bid") != d.get("bid")
                or prev.get("ask") != d.get("ask")
                or prev.get("last") != d.get("last")
                or prev.get("time") != d.get("time")
            ):
                changed.append(d)
            self._snapshot[sid] = d
        return changed

    async def broadcast(self, items: list[dict]) -> int:
        """Send each connection only the changed symbols it subscribes to."""
        by_symbol = {str(i.get("symbol_id") or ""): i for i in items}
        sent = 0
        for ws, syms in list(self._conn_symbols.items()):
            payload = [by_symbol[s] for s in syms if s in by_symbol]
            if payload and await self._send(ws, {"type": "quotes", "items": payload}):
                sent += 1
        return sent

    async def broadcast_error(self, detail: str) -> int:
        sent = 0
        for ws in list(self._conn_symbols):
            if await self._send(ws, {"type": "gateway_error", "detail": detail}):
                sent += 1
        return sent

    async def ping_all(self) -> int:
        sent = 0
        for ws in list(self._conn_symbols):
            if await self._send(ws, {"type": "ping"}):
                sent += 1
        return sent

    async def _send(self, ws: WebSocket, frame: dict) -> bool:
        try:
            await ws.send_json(frame)
            return True
        except Exception:  # noqa: BLE001 — dead/slow client: drop, never block the pump
            self.unregister(ws)  # noqa: ARG006 — unregister 非 async，不需 await
            return False

    # -- pump ------------------------------------------------------------------
    async def pump_once(self) -> float:
        """One poll cycle.  Returns seconds to sleep before the next cycle."""
        names = self.subscribed_symbols()
        if not names:
            return settings.MARKET_PUSH_POLL_SECONDS
        try:
            raw = await self.gateway.quotes(names)
        except GatewayError as exc:
            await self.broadcast_error(exc.detail)
            self.error_backoff = min(
                self.error_backoff * 2, ERROR_BACKOFF_MAX_SECONDS
            )
            return self.error_backoff
        dumps = [map_quote(row).model_dump(mode="json") for row in raw]
        changed = self.detect_changed(dumps)
        if changed:
            await self.broadcast(changed)
        self.error_backoff = 1.0
        return settings.MARKET_PUSH_POLL_SECONDS


async def pump_loop(hub: QuoteHub) -> None:
    """Lifespan task — never dies on an unexpected error."""
    while True:
        try:
            delay = await hub.pump_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("quote pump iteration failed")
            delay = hub.error_backoff
        await asyncio.sleep(delay)


async def ping_loop(hub: QuoteHub) -> None:
    """Keepalive sweep — lets clients tell「休市无变化」from a dead socket."""
    while True:
        await asyncio.sleep(PING_INTERVAL_SECONDS)
        try:
            await hub.ping_all()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("quote ping sweep failed")


# ---------------------------------------------------------------------------
# Module-level singleton (created in lifespan; shared by REST + WS endpoints)
# ---------------------------------------------------------------------------
_hub: QuoteHub | None = None


def get_quote_hub() -> QuoteHub | None:
    return _hub


def create_quote_hub() -> QuoteHub:
    global _hub
    if _hub is None:
        _hub = QuoteHub()
    return _hub


async def shutdown_quote_hub() -> None:
    global _hub
    if _hub is not None:
        await _hub.gateway.aclose()
        _hub = None
