"""``BacktestContext`` — the runtime ``ctx`` object strategies receive (M4).

Implements the SDK surface from ``shared/sdk/context.py`` against the
replay engine.  Key contracts enforced here (SDK spec §Context):

* ``get_bars`` is **truncated to the current bar index** — future data is
  physically absent from the window (runtime execution of the no-lookahead
  contract), clamped to 5000 visible bars.
* ``declare`` must be called in ``on_init`` exactly once; re-declaring or
  calling it later raises.
* Orders during the warmup window are rejected with WARMUP_PERIOD.
* ``buy``/``sell``/``close`` route through the engine (fill model +
  account sim) and synchronously re-enter ``on_order`` inside the call
  stack; more than 20 order calls per bar → RATE_LIMITED.
* ``storage`` is capped at 1 MB (STORAGE_FULL), ``log`` is rate-limited
  to 100 lines/second (excess lines are dropped with a warning).
* ``now`` / ``current_bar`` reflect the bar being processed — the only
  clock strategies can see.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Optional

from sdk.context import (
    AccountInfo,
    Bar,
    OrderResult,
    Position,
    Quote,
    StrategyLogger,
    StrategyStorage,
    SymbolInfo,
)
from sdk.errors import ErrorCode

if TYPE_CHECKING:
    from app.sandbox.engine import BacktestEngine

_MAX_VISIBLE_BARS = 5000
_STORAGE_QUOTA_BYTES = 1_000_000
_LOG_RATE_LIMIT = 100  # lines per wall-clock second
_MAX_ORDERS_PER_BAR = 20


def _epoch_to_dt(epoch_s: int) -> datetime:
    return datetime.fromtimestamp(epoch_s, tz=UTC)


class _BacktestStorage(StrategyStorage):
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def _size(self) -> int:
        return len(json.dumps(self._data, ensure_ascii=False, default=str).encode("utf-8"))

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        probe = dict(self._data)
        probe[key] = value
        size = len(json.dumps(probe, ensure_ascii=False, default=str).encode("utf-8"))
        if size > _STORAGE_QUOTA_BYTES:
            raise _StorageFullError(
                f"ctx.storage 超过 1MB 配额（当前 {size} 字节，STORAGE_FULL）"
            )
        self._data = probe

    def __delitem__(self, key: str) -> None:
        self._data.pop(key, None)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def keys(self):
        return self._data.keys()

    def clear(self) -> None:
        self._data.clear()

    @property
    def size_bytes(self) -> int:
        return self._size()


class _StorageFullError(Exception):
    pass


class _BacktestLogger(StrategyLogger):
    def __init__(self, engine: "BacktestEngine") -> None:
        self._engine = engine

    def info(self, message: str) -> None:
        self._engine.emit_log("info", str(message))

    def warn(self, message: str) -> None:
        self._engine.emit_log("warn", str(message))

    def error(self, message: str) -> None:
        self._engine.emit_log("error", str(message))


class BacktestContext:
    """The object passed as ``ctx`` to strategy callbacks."""

    mode = "backtest"

    def __init__(self, engine: "BacktestEngine") -> None:
        self._engine = engine
        self._declared = False
        self._symbol = ""
        self._period = "H1"
        self._params: dict[str, object] = {}
        self._storage = _BacktestStorage()
        self._log = _BacktestLogger(engine)
        self._orders_this_bar = 0

    # ── runtime metadata ───────────────────────────────────────

    @property
    def sdk_version(self) -> str:
        from sdk import __version__

        return __version__

    @property
    def strategy_id(self) -> str:
        return self._engine.payload.strategy_id

    # ── declaration ────────────────────────────────────────────

    def declare(
        self,
        symbol: str,
        period: str = "H1",
        params: dict[str, object] | None = None,
    ) -> None:
        if self._declared:
            raise RuntimeError("declare() 只能调用一次（on_init 内）")
        if self._engine.phase != "on_init":
            raise RuntimeError("declare() 必须在 on_init 中调用")
        declared_symbol = str(symbol)
        declared_period = str(period)
        # Task-level overrides (payload.symbol / payload.timeframe, chosen at
        # deploy time) take precedence over strategy-declared defaults — same
        # rule as parameters below. Template defaults only fill gaps.
        task_symbol = str(getattr(self._engine.payload, "symbol", "") or "")
        task_period = str(getattr(self._engine.payload, "timeframe", "") or "")
        if task_symbol and task_symbol != declared_symbol:
            self._engine.emit_log(
                "warn",
                f"品种以任务配置为准：模板声明的 {declared_symbol} 被覆盖为 {task_symbol}",
            )
        self._symbol = task_symbol or declared_symbol
        if task_period and task_period.upper() != declared_period.upper():
            self._engine.emit_log(
                "warn",
                f"周期以任务配置为准：模板声明的 {declared_period} 被覆盖为 {task_period}",
            )
        self._period = task_period or declared_period
        # Task-level overrides (payload.parameters, e.g. parameter-optimization
        # combos) take precedence over the strategy-declared defaults.
        self._params = {**dict(params or {}), **dict(self._engine.payload.parameters)}
        self._declared = True

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def period(self) -> str:
        return self._period

    @property
    def params(self) -> dict[str, object]:
        return self._params

    # ── market data ────────────────────────────────────────────

    def get_bars(
        self,
        symbol: str,
        period: str,
        count: int = 100,
        *,
        end_time: Optional[datetime] = None,
    ) -> list[Bar]:
        if symbol != self._symbol or period != self._period:
            return []  # undeclared symbol/period → empty per SDK spec
        eng = self._engine
        count = max(0, min(int(count), _MAX_VISIBLE_BARS))
        end_idx = eng.index
        if end_time is not None:
            # Truncate to end_time as well, but never beyond the current bar.
            target = int(end_time.timestamp())
            while end_idx > 0 and eng.bars.time[end_idx] > target:
                end_idx -= 1
        start = max(0, end_idx - count + 1)
        out: list[Bar] = []
        for i in range(start, end_idx + 1):
            out.append(self._bar_at(i))
        return out

    def _bar_at(self, i: int) -> Bar:
        t, o, h, lo, c, v, sp = self._engine.bars.at(i)
        point = self._engine.symbol_info.point
        return Bar(
            symbol=self._symbol,
            period=self._period,
            time=_epoch_to_dt(t),
            open=o, high=h, low=lo, close=c,
            tick_volume=v,
            spread=sp * point,
        )

    def get_quote(self, symbol: str) -> Optional[Quote]:
        if symbol != self._symbol or self._engine.index < 0:
            return None
        eng = self._engine
        # During on_tick (paper/live) the quote comes from the live tick's
        # real bid/ask — bar-derived spread would be stale by seconds.
        if eng._last_tick is not None:
            msc, bid, ask = eng._last_tick
            bar_time_ms = int(eng.bars.time[eng.index]) * 1000
            if msc > bar_time_ms:
                return Quote(
                    symbol=symbol, bid=bid, ask=ask, spread=ask - bid,
                    time=_epoch_to_dt(msc // 1000),
                )
        i = eng.index
        _, _, _, _, close, _, sp_raw = eng.bars.at(i)
        spread_pts = eng.fill_model.spread_points_at(sp_raw)
        return Quote(
            symbol=symbol,
            bid=close,
            ask=close + spread_pts * eng.symbol_info.point,
            spread=spread_pts * eng.symbol_info.point,
            time=_epoch_to_dt(eng.bars.time[i]),
        )

    def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        if symbol != self._symbol:
            return None
        return self._engine.symbol_info

    # ── account ────────────────────────────────────────────────

    def get_account(self) -> AccountInfo:
        return self._engine.account_info()

    def get_positions(self) -> list[Position]:
        return self._engine.snapshot_positions()

    def get_position(self, position_id: str) -> Optional[Position]:
        for p in self._engine.snapshot_positions():
            if p.id == position_id:
                return p
        return None

    # ── orders ─────────────────────────────────────────────────

    def _order_gate(self) -> Optional[OrderResult]:
        eng = self._engine
        if eng.phase not in ("on_bar", "on_order", "on_tick"):
            return OrderResult(
                success=False,
                error_code=ErrorCode.STRATEGY_STOPPED,
                error_message="订单只能在 on_bar/on_order/on_tick 回调中下达",
            )
        if eng.index < eng.warmup_end:
            return OrderResult(
                success=False,
                error_code=ErrorCode.WARMUP_PERIOD,
                error_message=f"预热期内禁止下单（还需 {eng.warmup_end - eng.index} 根）",
            )
        self._orders_this_bar += 1
        if self._orders_this_bar > _MAX_ORDERS_PER_BAR:
            return OrderResult(
                success=False,
                error_code=ErrorCode.RATE_LIMITED,
                error_message=f"单根 K 线订单调用超过 {_MAX_ORDERS_PER_BAR} 次上限",
            )
        return None

    def buy(self, symbol: str, volume: float, *, comment: str = "") -> OrderResult:
        gate = self._order_gate()
        if gate:
            return gate
        if symbol != self._symbol:
            return OrderResult(
                success=False,
                error_code=ErrorCode.INVALID_SYMBOL,
                error_message=f"品种 {symbol!r} 未声明",
            )
        return self._engine.place_order("buy", float(volume), comment)

    def sell(self, symbol: str, volume: float, *, comment: str = "") -> OrderResult:
        gate = self._order_gate()
        if gate:
            return gate
        if symbol != self._symbol:
            return OrderResult(
                success=False,
                error_code=ErrorCode.INVALID_SYMBOL,
                error_message=f"品种 {symbol!r} 未声明",
            )
        return self._engine.place_order("sell", float(volume), comment)

    def close(
        self,
        position_id: str,
        *,
        volume: Optional[float] = None,
        comment: str = "",
    ) -> OrderResult:
        gate = self._order_gate()
        if gate:
            return gate
        return self._engine.place_close(str(position_id), volume, comment)

    # ── state / logging / time ─────────────────────────────────

    @property
    def storage(self) -> StrategyStorage:
        return self._storage

    @property
    def log(self) -> StrategyLogger:
        return self._log

    @property
    def now(self) -> datetime:
        eng = self._engine
        if eng.index < 0:
            return _epoch_to_dt(int(eng.bars.time[0])) if len(eng.bars) else datetime.now(UTC)
        return _epoch_to_dt(int(eng.bars.time[eng.index]))

    @property
    def current_bar(self) -> Optional[Bar]:
        eng = self._engine
        if eng.index < 0:
            return None
        return self._bar_at(eng.index)

    # ── engine-internal ────────────────────────────────────────

    def _reset_bar_counters(self) -> None:
        self._orders_this_bar = 0
