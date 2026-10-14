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

共享循环骨架（storage 三件套 / feed / feed_ticks / start / stop）在
:mod:`app.sandbox.incremental`；本文件只保留 paper 侧差异：上下文工厂、
停止强平、账户事件载荷。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterator

from app.sandbox.context import BacktestContext
from app.sandbox.engine import BacktestEngine, EngineEvent
from app.sandbox.incremental import IncrementalEngine, _iso


class PaperContext(BacktestContext):
    """Same surface as backtest, but the SDK reports ``mode == "paper"``."""

    mode = "paper"


class PaperEngine(IncrementalEngine):
    def _create_ctx(self) -> BacktestContext:
        return PaperContext(self.eng)

    def _close_on_stop(self, eng: BacktestEngine) -> Iterator[EngineEvent]:
        if len(eng.bars) and eng.index >= 0:
            yield from eng._force_close_at_last_bar()

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
                round(ml, 4) if (ml := acc.margin_level(close)) is not None else None
            ),
            "last_bar_time": (
                _iso(int(eng.bars.time[eng.index])) if eng.index >= 0 else None
            ),
            "positions": [
                asdict(p) for p in eng.snapshot_positions()
            ],
        })
