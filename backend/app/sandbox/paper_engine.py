"""PaperEngine — incremental bar-driven live loop for paper trading (M5).

Wraps the M4 :class:`BacktestEngine` (same FillModel / AccountSim / sandbox —
PRD 一致性原则) but drives it *incrementally*: the runner subprocess loads the
strategy once, replays the startup history (warmup still rejects orders), then
feeds newly **closed** bars one at a time as the parent delivers them over
stdin.  There is no end-of-run forced close — positions persist until the
user stops (optionally closing) or a stop-out fires.

Tier restriction: only ``standard`` / ``optimistic`` make sense here; the
conservative tier needs the *next* bar's open price, which a live loop cannot
know at signal time.

State-recovery contract (PRD §状态恢复): only ``ctx.storage`` survives a
re-spawn, persisted to ``storage.json`` in the payload directory after every
processed bar.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Optional

import numpy as np

from app.sandbox import engine as engine_mod
from app.sandbox.context import BacktestContext
from app.sandbox.engine import BacktestEngine, EngineEvent
from app.sandbox.import_hook import SandboxViolationError
from app.sandbox.matching import TF_SECONDS
from app.sandbox.payload import BacktestPayload, BarArrays

STORAGE_FILE = "storage.json"
_MAX_EQUITY_POINTS = 5000


def _iso(epoch_s: int) -> str:
    return datetime.fromtimestamp(int(epoch_s), tz=UTC).isoformat()


class PaperContext(BacktestContext):
    """Same surface as backtest, but the SDK reports ``mode == "paper"``."""

    mode = "paper"


class PaperEngine:
    def __init__(
        self,
        payload: BacktestPayload,
        bars: BarArrays,
        storage_path: Optional[Path] = None,
    ) -> None:
        self.payload = payload
        self.eng = BacktestEngine(payload, bars)
        self.eng.ctx = PaperContext(self.eng)
        self.storage_path = storage_path
        self.live = False
        self._tf_seconds = TF_SECONDS[payload.timeframe]

    # ── storage persistence (recovery contract) ────────────────

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

    # ── account mirror event ───────────────────────────────────

    def account_event(self) -> EngineEvent:
        eng = self.eng
        close = eng._cur_close() if len(eng.bars) else 0.0
        acc = eng.account
        return EngineEvent("account", {
            "balance": round(acc.balance, 4),
            "equity": round(acc.equity(close), 4),
            "margin_used": round(acc.margin_used(close), 4),
            "margin_free": round(acc.free_margin(close), 4),
            "unrealized_pnl": round(acc.unrealized_pnl(close), 4),
            "margin_level": (
                round(acc.margin_level(close), 4) if acc.margin_level(close) else None
            ),
            "last_bar_time": (
                _iso(int(eng.bars.time[eng.index])) if eng.index >= 0 else None
            ),
            "positions": [
                asdict(p) for p in eng.snapshot_positions()
            ],
        })

    # ── lifecycle ──────────────────────────────────────────────

    def start(self) -> Iterator[EngineEvent]:
        """Load strategy → on_init → replay startup history (no forced close)."""
        eng = self.eng
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
                "code": "RUNTIME_ERROR",
                "message": f"策略装载失败：{exc}",
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

    def feed(self, rows: list[list[float]]) -> Iterator[EngineEvent]:
        """Append newly closed bars (rows = [time,open,high,low,close,vol,spread])
        and run one on_bar cycle per row; stale/duplicate times are skipped."""
        eng = self.eng
        if eng._halted or len(eng.bars) == 0:
            return
        for t, o, h, lo, c, v, sp, *rest in rows:
            if int(t) <= int(eng.bars.time[-1]):
                continue  # duplicate / stale bar
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

        Batch-end persistence: storage/account snapshot/drain once per batch,
        not per tick (ticks can arrive at tens per second).
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
        """Terminate: optionally force-close at last close, call on_stop,
        persist storage, emit the final account snapshot."""
        eng = self.eng
        if close_positions and len(eng.bars) and eng.index >= 0:
            yield from eng._force_close_at_last_bar()
        try:
            eng._call("on_stop", eng.ctx, "stopped",
                      timeout=eng.payload.callback_timeout_seconds)
        except Exception:
            pass
        eng.phase = "stop"
        self._persist_storage()
        yield self.account_event()
        yield from eng._drain()
