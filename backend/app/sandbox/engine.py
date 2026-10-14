"""BacktestEngine — bar-driven replay loop (M4 core).

The engine is a **pure library**: it takes a payload + bar arrays and yields
:class:`EngineEvent` objects; it never touches the database or the network.
The runner subprocess is only a thin JSONL transport around it, and M5
paper trading can reuse the same loop.

Lifecycle:
    load strategy (restricted exec) → on_init (declare enforced) →
    per bar: ctx._advance → on_bar → orders fill (conservative = next
    open, synchronous on_order re-entry) → valuation + stop-out →
    end-of-run forced close → on_stop → done(report).

Safety errors (sandbox violation / callback timeout / unhandled exception)
terminate the run with a ``fail`` event; ``on_stop`` is attempted
best-effort.  All times are epoch seconds internally, ISO-8601 UTC on the
wire.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Iterator, Optional

from sdk.context import AccountInfo, OrderResult, Position, SymbolInfo, Tick
from sdk.errors import ErrorCode

from app.sandbox import metrics
from app.sandbox.context import BacktestContext
from app.sandbox.import_hook import (
    SandboxViolationError,
    install_all,
    restricted_module_dict,
)
from app.sandbox.matching import (
    AccountSim,
    Fill,
    FillModel,
    MatchResult,
)
from app.sandbox.payload import BacktestPayload, BarArrays

_TRACEBACK_LIMIT = 8192
_SYMBOL_INFO_DEFAULTS = {
    "display_name": "", "category": "", "base_currency": "USD",
    "quote_currency": "USD", "digits": 5, "point": 0.00001,
    "volume_step": 0.01, "volume_min": 0.01, "volume_max": 100.0,
    "contract_size": 100000.0, "margin_initial": 0.0, "trade_mode": "full",
}


def _iso(epoch_s: int) -> str:
    return datetime.fromtimestamp(int(epoch_s), tz=UTC).isoformat()


def _msc_to_dt(msc: int) -> datetime:
    return datetime.fromtimestamp(msc / 1000, tz=UTC)


@dataclass
class EngineEvent:
    kind: str  # progress | log | signal | trade | equity | done | fail
    data: dict = field(default_factory=dict)


class CallbackTimeoutError(Exception):
    pass


class BacktestEngine:
    def __init__(self, payload: BacktestPayload, bars: BarArrays) -> None:
        self.payload = payload
        self.bars = bars
        self.index = -1
        self.phase = "load"  # load | on_init | on_bar | on_order | stop | done
        self.warmup_end = payload.warmup_bars

        info = dict(_SYMBOL_INFO_DEFAULTS)
        info.update(payload.symbol_info or {})
        info["name"] = payload.symbol
        self.symbol_info = SymbolInfo(**info)

        self.fill_model = FillModel(
            mode=payload.execution_mode,
            point=self.symbol_info.point,
            spread_mode=payload.spread_mode,
            fixed_spread_points=float(payload.spread_points or 0.0),
            spread_snapshot=payload.spread_snapshot,
            slippage_points=payload.slippage_points_or_default,
        )
        self.account = AccountSim(
            initial_deposit=payload.initial_deposit,
            leverage=payload.leverage,
            contract_size=self.symbol_info.contract_size,
            point=self.symbol_info.point,
            volume_step=self.symbol_info.volume_step,
            volume_min=self.symbol_info.volume_min,
            volume_max=self.symbol_info.volume_max,
            commission_per_lot=payload.commission_per_lot,
            stop_out_level=payload.stop_out_level,
            swap_long=payload.swap_long,
            swap_short=payload.swap_short,
        )
        self.ctx = BacktestContext(self)

        self._out: list[EngineEvent] = []
        self._halted = False  # set by _process_bar when a fail event fired
        self._last_swap_date: date | None = None  # overnight-swap rollover marker
        self._has_on_tick = False  # set by _load_strategy (paper/live tick feed)
        self._last_tick: tuple[int, float, float] | None = None  # (msc, bid, ask)
        self._seq = 0
        self._log_dropped = 0
        self._log_window_start = 0.0
        self._log_window_count = 0
        self._equity_times: list[int] = []
        self._equity: list[float] = []
        self._balance: list[float] = []
        self._progress_pct = -1
        self._module: Optional[dict] = None

    # ── event helpers (used by ctx + engine internals) ─────────

    def _drain(self) -> Iterator[EngineEvent]:
        while self._out:
            yield self._out.pop(0)

    def emit_log(
        self,
        level: str,
        message: str,
        kind: str = "log",
        payload: dict | None = None,
    ) -> None:
        if self._seq >= self.payload.log_limit:
            self._log_dropped += 1
            return
        # Rate-limit strategy log lines: 100/second wall-clock.
        if kind == "log":
            import time as _t

            now = _t.perf_counter()
            if now - self._log_window_start >= 1.0:
                self._log_window_start = now
                self._log_window_count = 0
            self._log_window_count += 1
            if self._log_window_count > 100:
                self._log_dropped += 1
                return
        self._seq += 1
        bar_time = (
            int(self.bars.time[self.index]) if self.index >= 0 else None
        )
        self._out.append(EngineEvent("log", {
            "seq": self._seq,
            "bar_time": _iso(bar_time) if bar_time is not None else None,
            "kind": kind,
            "level": level,
            "message": message[:2000],
            "payload": payload,
        }))

    def _emit_signal(self, side: str, price: float, volume: float, pos_id: str) -> None:
        bar_time = int(self.bars.time[self.index]) if self.index >= 0 else None
        self.emit_log("info", f"{side} 成交 {volume} @ {price:.8f}", kind="signal", payload={
            "side": side, "price": price, "volume": volume,
            "position_id": pos_id, "bar_time": bar_time,
        })

    # ── account snapshots for ctx ──────────────────────────────

    def _cur_close(self) -> float:
        return float(self.bars.close[self.index]) if self.index >= 0 else float(self.bars.close[0])

    def account_info(self) -> AccountInfo:
        close = self._cur_close()
        used = self.account.margin_used(close)
        return AccountInfo(
            balance=self.account.balance,
            equity=self.account.equity(close),
            margin_free=self.account.free_margin(close),
            margin_used=used,
            currency=self.symbol_info.quote_currency,
            leverage=self.payload.leverage,
        )

    def snapshot_positions(self) -> list[Position]:
        close = self._cur_close()
        out: list[Position] = []
        for p in self.account.positions.values():
            out.append(Position(
                id=p.id,
                symbol=self.payload.symbol,
                direction=p.direction,
                volume=p.volume,
                open_price=p.open_price,
                current_price=close,
                unrealized_pnl=p.unrealized(close, self.account.contract_size),
                realized_pnl=0.0,
                swap=p.swap_accrued,
                commission=0.0,
                opened_at=datetime.fromtimestamp(p.open_time, tz=UTC),
            ))
        return out

    # ── order placement (called via ctx) ───────────────────────

    def _normal_fill(self, side: str) -> MatchResult:
        # on_tick phase (paper signal layer): fill at the live tick — buy
        # crosses the tick spread (ask + slippage), sell receives bid − slip.
        if self.phase == "on_tick" and self._last_tick is not None:
            _, bid, ask = self._last_tick
            slip_p = self.fill_model.slippage_points * self.fill_model.point
            if side == "buy":
                return MatchResult(success=True, fill=Fill(
                    price=ask + slip_p, base_price=bid,
                    spread_cost=ask - bid, slippage_cost=slip_p,
                ))
            return MatchResult(success=True, fill=Fill(
                price=bid - slip_p, base_price=bid,
                spread_cost=0.0, slippage_cost=slip_p,
            ))
        i = self.index
        cur_close = float(self.bars.close[i])
        cur_spread = int(self.bars.spread[i])
        if self.payload.execution_mode == "conservative":
            if i + 1 < len(self.bars):
                next_open = float(self.bars.open[i + 1])
                next_spread = int(self.bars.spread[i + 1])
            else:
                next_open, next_spread = None, 0
            return self.fill_model.fill_for(
                side, cur_close, next_open, next_spread, cur_spread
            )
        return self.fill_model.fill_for(side, cur_close, None, 0, cur_spread)

    def place_order(self, side: str, volume: float, comment: str) -> OrderResult:
        vol, err = self.account.normalize_volume(volume)
        if err is not None:
            return OrderResult(
                success=False, error_code=err.error_code, error_message=err.error_message
            )
        result = self._normal_fill(side)
        if not result.success or result.fill is None:
            return OrderResult(
                success=False, error_code=result.error_code, error_message=result.error_message
            )
        fill = result.fill
        margin = self.account.margin_for(vol, fill.price)
        close = self._cur_close()
        if margin > self.account.free_margin(close):
            return OrderResult(
                success=False,
                error_code=ErrorCode.INSUFFICIENT_MARGIN,
                error_message=(
                    f"可用保证金不足：需要 {margin:.2f}，"
                    f"可用 {self.account.free_margin(close):.2f}"
                ),
            )
        opened = self.account.open_position(
            side, fill, vol, int(self.bars.time[self.index]), comment
        )
        assert opened.position_id is not None
        self._emit_signal(side, fill.price, vol, opened.position_id)
        order = OrderResult(
            success=True,
            order_id=opened.position_id,
            filled_volume=vol,
            filled_price=fill.price,
            commission=self.account.commission_per_lot * vol,
        )
        self._call_on_order(order)
        return order

    def place_close(self, position_id: str, volume: Optional[float], comment: str) -> OrderResult:
        pos = self.account.positions.get(position_id)
        if pos is None:
            return OrderResult(
                success=False,
                error_code=ErrorCode.POSITION_NOT_FOUND,
                error_message=f"持仓 {position_id} 不存在",
            )
        close_side = "sell" if pos.direction == "buy" else "buy"
        result = self._normal_fill(close_side)
        if not result.success or result.fill is None:
            return OrderResult(
                success=False, error_code=result.error_code, error_message=result.error_message
            )
        closed = self.account.close_position(
            position_id, result.fill, int(self.bars.time[self.index]),
            volume=volume, comment=comment,
        )
        if not closed.success:
            return OrderResult(
                success=False, error_code=closed.error_code, error_message=closed.error_message
            )
        assert closed.trade is not None and closed.fill is not None
        self._emit_signal(
            f"close_{pos.direction}", closed.fill.price, closed.volume, position_id
        )
        self._out.append(EngineEvent("trade", closed.trade))
        order = OrderResult(
            success=True,
            order_id=position_id,
            filled_volume=closed.volume,
            filled_price=closed.fill.price,
            commission=self.account.commission_per_lot * closed.volume,
        )
        self._call_on_order(order)
        return order

    def _forced_fill(self, side: str) -> Fill:
        """Forced closes execute at the current close price in every tier."""
        i = self.index
        cur_close = float(self.bars.close[i])
        cur_spread = int(self.bars.spread[i])
        res = self.fill_model.fill_for(side, cur_close, None, 0, cur_spread)
        if res.success and res.fill is not None:
            return res.fill
        return Fill(price=cur_close, base_price=cur_close, spread_cost=0.0, slippage_cost=0.0)

    # ── strategy loading & callbacks ───────────────────────────

    def _load_strategy(self) -> dict:
        install_all()
        module_dict = restricted_module_dict("strategy_main")
        compiled = compile(self.payload.source_code, "<strategy>", "exec")
        exec(compiled, module_dict)  # noqa: S102 — sandboxed by design
        for name in ("on_init", "on_bar", "on_order"):
            if not callable(module_dict.get(name)):
                raise RuntimeError(f"策略缺少必需回调 {name}()")
        self._has_on_tick = callable(module_dict.get("on_tick"))
        self._module = module_dict
        return module_dict

    def _call(self, fn_name: str, *args, timeout: float) -> None:
        fn = self._module.get(fn_name) if self._module else None
        if fn is None:
            return
        import signal
        import threading
        import time

        start = time.perf_counter()
        use_alarm = (
            timeout > 0
            and hasattr(signal, "SIGALRM")
            and threading.current_thread() is threading.main_thread()
        )
        if use_alarm:
            def _on_alarm(signum, frame):  # noqa: ANN001
                raise CallbackTimeoutError(
                    f"{fn_name}() 回调执行超过 {timeout:.0f}s，被强制中断"
                )

            old = signal.signal(signal.SIGALRM, _on_alarm)
            signal.setitimer(signal.ITIMER_REAL, timeout)
        try:
            fn(*args)
            elapsed = time.perf_counter() - start
            if elapsed > timeout:
                raise CallbackTimeoutError(
                    f"{fn_name}() 回调耗时 {elapsed:.2f}s 超过上限 {timeout:.0f}s"
                )
        finally:
            if use_alarm:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, old)

    def _call_on_order(self, order: OrderResult) -> None:
        prev = self.phase
        self.phase = "on_order"
        try:
            self._call(
                "on_order", self.ctx, order,
                timeout=self.payload.callback_timeout_seconds,
            )
        finally:
            self.phase = prev

    # ── tick feed (paper/live only; the backtest never calls on_tick) ──

    def feed_ticks(self, rows: list[list[float]]) -> Iterator[EngineEvent]:
        """Run one ``on_tick`` cycle per row (rows = [time_msc, bid, ask]).

        Sets ``_last_tick`` so ``ctx.get_quote`` reports the live spread and
        ``_normal_fill`` fills at tick bid/ask.  No bar append / valuation /
        stop-out / swap — those stay bar-driven.  Sets ``_halted`` on fail.
        """
        if self._halted or not self._has_on_tick or not rows:
            return
        for msc, bid, ask in rows:
            self._last_tick = (int(msc), float(bid), float(ask))
            self.ctx._reset_bar_counters()
            self.phase = "on_tick"
            tick = Tick(
                symbol=self.payload.symbol,
                time=_msc_to_dt(int(msc)),
                bid=float(bid),
                ask=float(ask),
            )
            try:
                self._call(
                    "on_tick", self.ctx, tick,
                    timeout=self.payload.callback_timeout_seconds,
                )
            except SandboxViolationError as exc:
                yield from self._fail(exc.code.value, exc.message, exc, call_stop=True)
                return
            except CallbackTimeoutError as exc:
                yield from self._fail(
                    ErrorCode.RESOURCE_TIMEOUT.value, str(exc), exc, call_stop=True
                )
                return
            except Exception as exc:
                yield from self._fail(
                    ErrorCode.RUNTIME_ERROR.value, str(exc), exc, call_stop=True
                )
                return
            if self._halted:
                return

    # ── main loop ──────────────────────────────────────────────

    def run(self) -> Iterator[EngineEvent]:
        n = len(self.bars)
        try:
            self._load_strategy()
        except SandboxViolationError as exc:
            yield EngineEvent("fail", {
                "code": exc.code.value, "message": exc.message,
                "traceback": "", "load_failed": True,
            })
            return
        except Exception as exc:
            yield EngineEvent("fail", {
                "code": ErrorCode.RUNTIME_ERROR.value,
                "message": f"策略装载失败：{exc}",
                "traceback": self._tb(exc), "load_failed": True,
            })
            return

        # on_init
        self.phase = "on_init"
        try:
            self._call("on_init", self.ctx, timeout=self.payload.on_init_timeout_seconds)
        except SandboxViolationError as exc:
            yield from self._fail(exc.code.value, exc.message, exc)
            return
        except CallbackTimeoutError as exc:
            yield from self._fail(ErrorCode.RESOURCE_TIMEOUT.value, str(exc), exc)
            return
        except Exception as exc:
            yield from self._fail(ErrorCode.RUNTIME_ERROR.value, str(exc), exc)
            return
        if not self.ctx._declared:
            yield EngineEvent("fail", {
                "code": ErrorCode.RUNTIME_ERROR.value,
                "message": "策略必须在 on_init 中调用 ctx.declare()",
                "traceback": "",
            })
            return
        yield from self._drain()

        if n == 0:
            yield EngineEvent("fail", {
                "code": ErrorCode.UNKNOWN_ERROR.value,
                "message": "回测区间内没有 K 线数据",
                "traceback": "",
            })
            return

        # per-bar replay
        for i in range(n):
            self.index = i
            yield from self._process_bar()
            if self._halted:
                return

            pct = int((i + 1) / n * 100)
            if pct > self._progress_pct:
                self._progress_pct = pct
                self._out.append(EngineEvent("progress", {"percent": pct}))
            yield from self._drain()

        # end of run: forced close at final bar
        yield from self._force_close_at_last_bar()
        try:
            self._call("on_stop", self.ctx, "completed",
                       timeout=self.payload.callback_timeout_seconds)
        except Exception:
            pass
        yield from self._drain()

        yield EngineEvent("done", {"report": self._build_report()})
        self.phase = "done"

    def _force_close_at_last_bar(self) -> Iterator[EngineEvent]:
        """Force-close every open position at the current bar's close price."""
        self.phase = "stop"
        n = len(self.bars)
        forced = self.account.force_close_all(
            int(self.bars.time[n - 1]),
            lambda side: MatchResult(
                success=True, fill=self._forced_fill("sell" if side == "buy" else "buy")
            ),
        )
        for res in forced:
            if res.trade is not None:
                self._out.append(EngineEvent("trade", res.trade))
                self.emit_log(
                    "warn", "收盘强制平仓（end-of-run）", kind="system"
                )
        yield from self._drain()

    def _accrue_swap(self, i: int) -> None:
        """Overnight-swap rollover (P1 隔夜利息).

        At the first bar of each new UTC day, charge one night of swap on
        every open position — three nights on Wednesday (covers the weekend)
        and none on Saturday/Sunday rollovers, the standard FX broker
        convention.  Runs *before* on_bar so a position opened during this
        bar is not charged for its opening night.
        """
        t = int(self.bars.time[i])
        day = datetime.fromtimestamp(t, tz=UTC).date()
        if day == self._last_swap_date:
            return
        self._last_swap_date = day
        wd = day.weekday()  # 0=Mon … 6=Sun
        if wd >= 5:
            return  # weekend rollover: no charge (Wednesday carries ×3)
        nights = 3 if wd == 2 else 1
        self.account.accrue_swap(t, nights)

    def _process_bar(self) -> Iterator[EngineEvent]:
        """Run one bar: swap rollover → on_bar callback → valuation + stop-out.

        Shared by the backtest replay loop and the M5 paper incremental loop.
        Sets ``_halted`` when a fail event is produced (the caller must stop
        feeding further bars).
        """
        i = self.index
        self.ctx._reset_bar_counters()
        self._accrue_swap(i)
        self.phase = "on_bar"
        bar = self.ctx._bar_at(i)
        try:
            self._call("on_bar", self.ctx, bar,
                       timeout=self.payload.callback_timeout_seconds)
        except SandboxViolationError as exc:
            yield from self._fail(exc.code.value, exc.message, exc, call_stop=True)
            return
        except CallbackTimeoutError as exc:
            yield from self._fail(
                ErrorCode.RESOURCE_TIMEOUT.value, str(exc), exc, call_stop=True
            )
            return
        except Exception as exc:
            yield from self._fail(ErrorCode.RUNTIME_ERROR.value, str(exc), exc, call_stop=True)
            return

        # valuation + stop-out at this bar's close
        close = float(self.bars.close[i])
        self._equity_times.append(int(self.bars.time[i]))
        self._equity.append(self.account.equity(close))
        self._balance.append(self.account.balance)
        stop = self.account.check_stop_out(
            close, int(self.bars.time[i]),
            lambda side: MatchResult(
                success=True, fill=self._forced_fill("sell" if side == "buy" else "buy")
            ),
        )
        for res in stop:
            if res.trade is not None:
                self._out.append(EngineEvent("trade", res.trade))
                self.emit_log(
                    "warn",
                    f"强制平仓（stop-out）持仓 {res.position_id} "
                    f"@ {res.fill.price:.8f}" if res.fill else "强制平仓",
                    kind="system",
                )

    def _fail(
        self, code: str, message: str, exc: BaseException, call_stop: bool = False
    ) -> Iterator[EngineEvent]:
        self._halted = True
        if call_stop and self._module is not None:
            try:
                self.phase = "stop"
                fn = self._module.get("on_stop")
                if callable(fn):
                    fn(self.ctx, f"failed: {code}")
            except Exception:
                pass
        self.emit_log("error", f"回测终止 [{code}]：{message}", kind="system")
        yield from self._drain()
        yield EngineEvent("fail", {
            "code": code, "message": message[:2000], "traceback": self._tb(exc),
        })

    @staticmethod
    def _tb(exc: BaseException) -> str:
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        return text[:_TRACEBACK_LIMIT]

    # ── report ─────────────────────────────────────────────────

    def _build_report(self) -> dict:
        eq = self._equity
        times = self._equity_times
        deposit = self.payload.initial_deposit
        tf = self.payload.timeframe
        dd_amount, dd_pct = metrics.max_drawdown(eq)
        stats = metrics.trade_stats(self.account.trades)
        total_return = (eq[-1] - deposit) if eq else 0.0

        idxs = metrics.downsample_indices(len(times), 1500)
        closes = [float(self.bars.close[i]) for i in range(len(self.bars))]
        baseline_full = metrics.buy_hold_baseline(
            [int(t) for t in self.bars.time], closes, deposit
        )
        eq_by_time = dict(zip(times, eq))
        bal_by_time = dict(zip(times, self._balance))
        run_peak = -float("inf")
        curve = []
        for i in idxs:
            t = times[i]
            v = eq_by_time[t]
            run_peak = max(run_peak, v)
            curve.append({
                "time": _iso(t),
                "equity": round(v, 4),
                "balance": round(bal_by_time[t], 4),
                "drawdown": round((run_peak - v) / run_peak * 100.0, 4) if run_peak > 0 else 0.0,
                "baseline": round(baseline_full[i], 4),
            })

        report = {
            "total_return": total_return,
            "total_return_percent": total_return / deposit * 100.0 if deposit > 0 else 0.0,
            "annual_return_percent": metrics.annual_return_percent(eq, tf),
            "annual_volatility_percent": metrics.annual_volatility_percent(eq, tf),
            "sharpe_ratio": metrics.sharpe_ratio(eq, tf),
            "sortino_ratio": metrics.sortino_ratio(eq, tf),
            "calmar_ratio": metrics.calmar_ratio(eq, tf),
            "recovery_factor": metrics.recovery_factor(eq, self.account.trades),
            "max_drawdown_percent": dd_pct * 100.0,
            "max_drawdown_amount": dd_amount,
            "monthly_returns": dict(metrics.monthly_returns(times, eq)),
            "equity_curve": curve,
            "final_balance": self.account.balance,
            "final_equity": eq[-1] if eq else deposit,
            "total_swap": round(sum(t["swap"] for t in self.account.trades), 4),
            "n_bars": len(self.bars),
            "log_dropped": self._log_dropped,
            **stats,
        }
        return report
