"""LiveEngine — real-money incremental loop (M6).

Same signal layer as backtest/paper (PRD 一致性原则): one BacktestEngine loop,
the same ``_order_gate`` (phase / warmup / per-bar order cap), the same SDK
surface.  The **only** difference is the execution boundary — ``ctx.buy/sell/
close`` do NOT touch the local AccountSim; they emit an ``order_request`` over
stdout and block for the parent's ``order_result`` (the parent runs the
pre-trade risk gate and forwards the order to the MT5 gateway).

Because the sandbox forbids network access, the subprocess cannot reach the
gateway itself; the parent owns every outbound call.  Account/positions are a
READ-ONLY mirror pushed by the parent (``{"cmd":"account"}`` and inside each
order_result), so ``ctx.get_account/get_positions`` reflect the real broker
state and strategy logic keeps working.

Warm-up: ``warmup_end`` is pinned to the full startup history length, so every
replayed bar is inside the warm-up window and orders are rejected with
WARMUP_PERIOD — history never trades.  Newly fed live bars (index >= that
length) are tradable.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Optional

import numpy as np
from sdk.context import AccountInfo, OrderResult, Position
from sdk.errors import ErrorCode

from app.sandbox import engine as engine_mod
from app.sandbox.context import BacktestContext
from app.sandbox.engine import BacktestEngine, EngineEvent
from app.sandbox.import_hook import SandboxViolationError
from app.sandbox.matching import TF_SECONDS
from app.sandbox.payload import BacktestPayload, BarArrays

STORAGE_FILE = "storage.json"


def _iso(epoch_s: int) -> str:
    return datetime.fromtimestamp(int(epoch_s), tz=UTC).isoformat()


class LiveContext(BacktestContext):
    """``mode == "live"``; orders route through the parent RPC transport."""

    mode = "live"

    def __init__(self, live: "LiveEngine") -> None:
        # base gate/bars/phase machinery reads the BacktestEngine; the live
        # engine supplies the RPC transport and the broker mirror.
        super().__init__(live.eng)
        self._live: LiveEngine = live

    # ── account / positions read the real broker mirror ──────────
    def get_account(self) -> AccountInfo:
        m = self._live.mirror.get("account") or {}
        return AccountInfo(
            balance=float(m.get("balance", 0.0)),
            equity=float(m.get("equity", 0.0)),
            margin_free=float(m.get("margin_free", 0.0)),
            margin_used=float(m.get("margin", 0.0)),
            currency=str(m.get("currency", self._live.eng.symbol_info.quote_currency)),
            leverage=int(m.get("leverage", self._live.eng.payload.leverage)),
        )

    def get_positions(self) -> list[Position]:
        out: list[Position] = []
        for p in self._live.mirror.get("positions") or []:
            out.append(Position(
                id=str(p["ticket"]),
                symbol=p["symbol"],
                direction=p["type"],
                volume=float(p["volume"]),
                open_price=float(p["price_open"]),
                current_price=float(p["price_current"]),
                unrealized_pnl=float(p["profit"]),
                realized_pnl=0.0,
                swap=float(p.get("swap", 0.0)),
                commission=float(p.get("commission", 0.0)),
                opened_at=datetime.fromtimestamp(int(p["time"]), tz=UTC),
            ))
        return out

    def get_position(self, position_id: str) -> Optional[Position]:
        for p in self.get_positions():
            if p.id == str(position_id):
                return p
        return None

    # ── orders: gate locally, execute remotely ──────────────────
    def buy(self, symbol: str, volume: float, *, comment: str = "") -> OrderResult:
        gate = self._order_gate()
        if gate:
            return gate
        if symbol != self._symbol:
            return OrderResult(
                success=False, error_code=ErrorCode.INVALID_SYMBOL,
                error_message=f"品种 {symbol!r} 未声明",
            )
        return self._live.request_order("buy", symbol, float(volume), None, comment)

    def sell(self, symbol: str, volume: float, *, comment: str = "") -> OrderResult:
        gate = self._order_gate()
        if gate:
            return gate
        if symbol != self._symbol:
            return OrderResult(
                success=False, error_code=ErrorCode.INVALID_SYMBOL,
                error_message=f"品种 {symbol!r} 未声明",
            )
        return self._live.request_order("sell", symbol, float(volume), None, comment)

    def close(
        self, position_id: str, *, volume: Optional[float] = None, comment: str = ""
    ) -> OrderResult:
        gate = self._order_gate()
        if gate:
            return gate
        return self._live.request_order("close", self._symbol, volume, position_id, comment)


class LiveEngine:
    """Incremental live loop; delegates the pure signal machinery to BacktestEngine."""

    def __init__(
        self,
        payload: BacktestPayload,
        bars: BarArrays,
        transport,  # Transport from live_runner: request_order(...) + emit(...)
        storage_path: Optional[Path] = None,
    ) -> None:
        self.payload = payload
        self.eng = BacktestEngine(payload, bars)
        self.ctx = LiveContext(self)
        self.eng.ctx = self.ctx
        self.transport = transport
        self.storage_path = storage_path
        self.mirror: dict = {"account": {}, "positions": []}
        self.live = False
        self._tf_seconds = TF_SECONDS[payload.timeframe]

    # ── RPC ──────────────────────────────────────────────────────
    def request_order(
        self, op: str, symbol: str, volume: Optional[float],
        position_id: Optional[str], comment: str,
    ) -> OrderResult:
        """Emit an order_request, block for the parent's order_result, then
        re-enter on_order synchronously (mirrors backtest/paper)."""
        bar_time = (
            int(self.eng.bars.time[self.eng.index]) if self.eng.index >= 0 else None
        )
        # Signal-bar reference for the deviation report (M4): theoretical fill
        # is derived from the close/spread of the bar the signal fired on.
        idx = self.eng.index
        signal_price = float(self.eng.bars.close[idx]) if idx >= 0 else None
        signal_spread = int(self.eng.bars.spread[idx]) if idx >= 0 else None
        res = self.transport.request_order({
            "op": op, "symbol": symbol, "volume": volume,
            "position_id": position_id, "comment": comment,
            "bar_time": _iso(bar_time) if bar_time is not None else None,
            "signal_price": signal_price,
            "signal_spread": signal_spread,
        })
        if res.get("account"):
            self.apply_mirror(res["account"])
        order = self._to_order_result(res)
        if order.success:
            side = f"close_{position_id}" if op == "close" else op
            self.eng._emit_signal(
                side, order.filled_price or 0.0, order.filled_volume or 0.0,
                order.order_id or "",
            )
        self._call_on_order(order)
        return order

    @staticmethod
    def _to_order_result(res: dict) -> OrderResult:
        code = res.get("error_code")
        return OrderResult(
            success=bool(res.get("success")),
            order_id=str(res["order_id"]) if res.get("order_id") is not None else None,
            error_code=ErrorCode(code) if code else None,
            error_message=res.get("error_message"),
            filled_volume=res.get("filled_volume"),
            filled_price=res.get("filled_price"),
            commission=float(res.get("commission", 0.0) or 0.0),
        )

    def _call_on_order(self, order: OrderResult) -> None:
        eng = self.eng
        prev = eng.phase
        eng.phase = "on_order"
        try:
            eng._call(
                "on_order", eng.ctx, order,
                timeout=eng.payload.callback_timeout_seconds,
            )
        finally:
            eng.phase = prev

    def apply_mirror(self, data: dict) -> None:
        self.mirror = {
            "account": data.get("account") or {},
            "positions": data.get("positions") or [],
        }

    # ── storage persistence (recovery contract) ─────────────────
    def restore_storage(self) -> None:
        if self.storage_path is None or not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self.eng.ctx._storage._data.update(data)

    def _persist_storage(self) -> None:
        if self.storage_path is None:
            return
        try:
            self.storage_path.write_text(
                json.dumps(self.eng.ctx._storage._data, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except OSError:
            pass

    def set_storage(self, updates: dict) -> None:
        """Parent-injected storage update; persists immediately so the
        detail endpoint's ``strategy_state`` snapshot reflects it."""
        self.eng.ctx._storage._data.update(updates)
        self._persist_storage()

    def reset_anchor(self) -> float | None:
        """Re-anchor the grid template to the freshest quote (bid).

        Returns the new anchor, or None when no quote is available yet."""
        quote = self.eng.ctx.get_quote(self.eng.ctx.symbol)
        if quote is None:
            return None
        bid = float(quote.bid)
        self.set_storage({"anchor": bid})
        return bid

    # ── account mirror event (from real broker snapshot) ────────
    def account_event(self) -> EngineEvent:
        a = self.mirror.get("account") or {}
        return EngineEvent("account", {
            "balance": float(a.get("balance", 0.0)),
            "equity": float(a.get("equity", 0.0)),
            "margin_used": float(a.get("margin", 0.0)),
            "margin_free": float(a.get("margin_free", 0.0)),
            "unrealized_pnl": float(a.get("equity", 0.0)) - float(a.get("balance", 0.0)),
            "margin_level": float(a.get("margin_level", 0.0)) or None,
            "last_bar_time": (
                _iso(int(self.eng.bars.time[self.eng.index]))
                if self.eng.index >= 0 else None
            ),
            "positions": list(self.mirror.get("positions") or []),
        })

    # ── lifecycle ────────────────────────────────────────────────
    def start(self) -> Iterator[EngineEvent]:
        eng = self.eng
        # pin warmup_end to the whole startup history → replay never trades
        eng.warmup_end = len(eng.bars)
        try:
            eng._load_strategy()
        except SandboxViolationError as exc:
            yield EngineEvent("fail", {
                "code": exc.code.value, "message": exc.message,
                "traceback": "", "load_failed": True,
            })
            return
        except Exception as exc:
            yield EngineEvent("fail", {
                "code": "RUNTIME_ERROR", "message": f"策略装载失败：{exc}",
                "traceback": eng._tb(exc), "load_failed": True,
            })
            return

        eng.phase = "on_init"
        try:
            eng._call("on_init", eng.ctx, timeout=eng.payload.on_init_timeout_seconds)
        except SandboxViolationError as exc:
            yield from eng._fail(exc.code.value, exc.message, exc)
            return
        except engine_mod.CallbackTimeoutError as exc:
            yield from eng._fail("RESOURCE_TIMEOUT", str(exc), exc)
            return
        except Exception as exc:
            yield from eng._fail("RUNTIME_ERROR", str(exc), exc)
            return
        if not eng.ctx._declared:
            yield EngineEvent("fail", {
                "code": "RUNTIME_ERROR",
                "message": "策略必须在 on_init 中调用 ctx.declare()",
                "traceback": "",
            })
            return
        yield from eng._drain()

        n = len(eng.bars)
        for i in range(n):
            eng.index = i
            yield from eng._process_bar()
            if eng._halted:
                return
            self._persist_storage()
            yield self.account_event()
            yield from eng._drain()
        self.live = True
        # Ticks don't advance the bar index — lift the pinned warmup so on_tick
        # is tradable once the replay is over (new bars keep index > warmup_end;
        # replayed history can never be re-processed because feed() skips
        # stale/duplicate times).
        if len(eng.bars):
            eng.warmup_end = len(eng.bars) - 1

    def feed(self, rows: list[list[float]]) -> Iterator[EngineEvent]:
        eng = self.eng
        if eng._halted or len(eng.bars) == 0:
            return
        for t, o, h, lo, c, v, sp, *rest in rows:
            if int(t) <= int(eng.bars.time[-1]):
                continue
            eng.bars.time = np.append(eng.bars.time, int(t))
            eng.bars.open = np.append(eng.bars.open, float(o))
            eng.bars.high = np.append(eng.bars.high, float(h))
            eng.bars.low = np.append(eng.bars.low, float(lo))
            eng.bars.close = np.append(eng.bars.close, float(c))
            eng.bars.tick_volume = np.append(eng.bars.tick_volume, int(v))
            eng.bars.spread = np.append(eng.bars.spread, int(sp))
            eng.index = len(eng.bars) - 1
            yield from eng._process_bar()
            if eng._halted:
                return
            self._persist_storage()
            yield self.account_event()
            yield from eng._drain()

    def feed_ticks(self, rows: list[list[float]]) -> Iterator[EngineEvent]:
        """Deliver real ticks (rows = [time_msc, bid, ask]) to on_tick.

        Orders placed inside on_tick go through the same RPC transport as
        on_bar (parent risk gate → gateway).  Batch-end persistence mirrors
        the paper engine.
        """
        eng = self.eng
        if eng._halted or len(eng.bars) == 0 or not rows:
            return
        yield from eng.feed_ticks(rows)
        if eng._halted:
            return
        self._persist_storage()
        yield self.account_event()
        yield from eng._drain()

    def stop(self, close_positions: bool) -> Iterator[EngineEvent]:
        """Terminate. close_positions is executed by the PARENT (real closes),
        not here — the child only calls on_stop and persists storage."""
        eng = self.eng
        try:
            eng._call("on_stop", eng.ctx, "stopped",
                      timeout=eng.payload.callback_timeout_seconds)
        except Exception:
            pass
        eng.phase = "stop"
        self._persist_storage()
        yield self.account_event()
        yield from eng._drain()
