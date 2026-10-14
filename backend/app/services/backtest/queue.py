"""In-process asyncio backtest queue (M4 Phase D).

One singleton :class:`BacktestQueue` owns N workers (BACKTEST_MAX_CONCURRENCY)
consuming task ids from an :class:`asyncio.Queue`.  Worker pipeline per task:

    CAS claim (pending→running) → static compile (AST + compile();
    frozen versions get compiled_at/compile_errors written back) →
    gateway symbol-info + paginated bar fetch → payload dir write →
    RunnerSupervisor.spawn + JSONL event persistence (progress throttled,
    trades/logs batch-flushed) → terminal state → temp dir cleanup.

Cancellation: pending rows are CAS'd straight to ``cancelled``; running
rows go to ``cancelling`` and the supervisor kills the process tree —
the worker then finalises ``cancelled``.  ``startup_sweep`` marks
orphaned running rows failed and re-enqueues pending ones.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import shutil
import time
import uuid as uuid_mod
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sqlalchemy import select, update

from app.config import settings
from app.engine.session import AsyncSessionLocal
from app.models import (
    BacktestLog,
    BacktestReport,
    BacktestTask,
    BacktestTrade,
    KlineCache,
    StrategyVersion,
)
from app.sandbox.matching import TF_SECONDS
from app.sandbox.payload import BacktestPayload, save_payload
from app.services.backtest.data_fetcher import (
    DataUnavailableError,
    bars_from_local,
    fetch_bars,
)
from app.services.backtest.supervisor import (
    TERM_CANCELLED,
    TERM_CRASH,
    TERM_FAILED,
    TERM_RESOURCE,
    TERM_TIMEOUT,
    RunnerSupervisor,
    SuperviseResult,
)
from app.services.fault_state import (  # P0-4 统一故障状态机
    FaultType,
    gate_fault,
)
from app.services.gateway_client import GatewayClient, GatewayError
from app.services.strategy_validator import validate_source

logger = logging.getLogger("mt5quant.backtest.queue")

_FLUSH_ROWS = 200
_PROGRESS_MIN_INTERVAL = 2.0  # seconds between progress writes

_TRADE_MODE_MAP = {0: "disabled", 1: "close_only", 4: "full"}


def _dec(v: object, places: int = 8) -> Decimal:
    return Decimal(str(round(float(v), places)))  # type: ignore[arg-type]


def _uuid(s: str) -> uuid_mod.UUID:
    return uuid_mod.UUID(s)


def _dt(epoch_or_iso: object) -> Optional[datetime]:
    if epoch_or_iso is None:
        return None
    if isinstance(epoch_or_iso, (int, float)):
        return datetime.fromtimestamp(int(epoch_or_iso), tz=UTC)
    return datetime.fromisoformat(str(epoch_or_iso))


class BacktestQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._supervisors: dict[str, RunnerSupervisor] = {}
        self._started = False

    # ── lifecycle ──────────────────────────────────────────────

    async def start(self) -> None:
        if self._started or not settings.BACKTEST_ENABLED:
            return
        self._started = True
        n = settings.BACKTEST_MAX_CONCURRENCY
        for i in range(n):
            self._workers.append(asyncio.create_task(self._worker(i), name=f"bt-worker-{i}"))
        logger.info("backtest queue started with %d workers", n)

    async def shutdown(self) -> None:
        self._started = False
        for sup in list(self._supervisors.values()):
            sup.request_cancel()
        for w in self._workers:
            w.cancel()
        for w in self._workers:
            try:
                await w
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("backtest worker terminated with error")
        self._workers.clear()
        logger.info("backtest queue stopped")

    def enqueue(self, task_id: str) -> None:
        self._queue.put_nowait(task_id)

    def snapshot(self) -> dict:
        """Queue overview (M18 任务队列监控): running ids + pending depth."""
        return {
            "running": sorted(self._supervisors.keys()),
            "pending_count": self._queue.qsize(),
        }

    async def cancel(self, task_id: str) -> str:
        """Request cancellation; returns the resulting status."""
        async with AsyncSessionLocal() as db:
            row = await db.get(BacktestTask, _uuid(task_id))
            if row is None:
                return "not_found"
            if row.status == "pending":
                res = await db.execute(
                    update(BacktestTask)
                    .where(BacktestTask.id == row.id, BacktestTask.status == "pending")
                    .values(status="cancelled", completed_at=datetime.now(UTC))
                )
                await db.commit()
                return "cancelled" if res.rowcount else row.status  # type: ignore[attr-defined]  # SQLAlchemy Result type annotation gap — real type has .rowcount/.scalars()
            if row.status in ("running", "cancelling"):
                await db.execute(
                    update(BacktestTask)
                    .where(BacktestTask.id == row.id, BacktestTask.status == "running")
                    .values(status="cancelling")
                )
                await db.commit()
                sup = self._supervisors.get(task_id)
                if sup is not None:
                    sup.request_cancel()
                return "cancelling"
            return row.status  # terminal — immutable

    async def startup_sweep(self) -> None:
        """Reconcile DB state after a restart: running→failed, pending→requeue."""
        now = datetime.now(UTC)
        async with AsyncSessionLocal() as db:
            # 先查出受影响的 task id，bulk update 后逐个 emit 结构化故障
            affected_ids = list(
                await db.scalars(
                    select(BacktestTask.id)
                    .where(BacktestTask.status.in_(["running", "cancelling"]))
                )
            )
            await db.execute(
                update(BacktestTask)
                .where(BacktestTask.status.in_(["running", "cancelling"]))
                .values(
                    status="failed",
                    completed_at=now,
                    error_code="UNKNOWN_ERROR",
                    error_message="服务重启导致任务中断，请重新发起回测",
                )
            )
            pending = list(
                await db.scalars(
                    select(BacktestTask.id).where(BacktestTask.status == "pending")
                )
            )
            await db.commit()
            # P0-4 结构化故障输出：批量 emit 服务重启导致的中断
            for tid in affected_ids:
                await gate_fault(
                    fault_type=FaultType.STARTUP_FAILED,
                    action_taken="服务重启导致回测任务中断，标记为失败",
                    run_id=tid,
                    detail="服务重启导致任务中断，请重新发起回测",
                ).emit(db=db)
        for tid in pending:
            self.enqueue(str(tid))
        if pending:
            logger.info("re-enqueued %d pending backtest tasks", len(pending))

    # ── worker ─────────────────────────────────────────────────

    async def _worker(self, idx: int) -> None:
        while True:
            task_id = await self._queue.get()
            try:
                await self._process(task_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — worker must survive
                logger.exception("backtest task %s processing crashed", task_id)
                await self._safe_fail(task_id, "UNKNOWN_ERROR", "任务编排异常，请重试")
            finally:
                self._queue.task_done()

    async def _process(self, task_id: str) -> None:
        uid = _uuid(task_id)
        # 1. CAS claim pending → running
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                update(BacktestTask)
                .where(BacktestTask.id == uid, BacktestTask.status == "pending")
                .values(status="running", started_at=datetime.now(UTC))
            )
            await db.commit()
            if not res.rowcount:  # type: ignore[attr-defined]  # SQLAlchemy Result type annotation gap — real type has .rowcount/.scalars()
                return  # already cancelled / not pending
            task = await db.get(BacktestTask, uid)
            assert task is not None
            source, errors = self._compile(task)
            if errors is not None:
                await self._write_version_compile(db, task, errors)
                await self._finish_failed(
                    db, task, "RUNTIME_ERROR",
                    "策略编译未通过：" + "；".join(errors)[:1800],
                )
                return
            await self._write_version_compile(db, task, None)
            await db.commit()
            task_id_str = str(task_id)
            payload_dir = Path(settings.BACKTEST_RUNTIME_DIR) / task_id_str

        # 2. gateway data + symbol info
        client = GatewayClient()
        try:
            try:
                symbols = await client.symbols()
            except GatewayError as exc:
                await self._safe_fail(task_id, "UNKNOWN_ERROR", f"网关不可达：{exc.detail}")
                return
            info = next(
                (s for s in symbols if s.get("name", "").upper() == task.symbol.upper()),
                None,
            )
            if info is None:
                await self._safe_fail(
                    task_id, "INVALID_SYMBOL", f"网关不存在品种：{task.symbol}"
                )
                return
            try:
                if task.data_source == "local":
                    # 本地数据来源：优先读全局共享K线缓存表（kline_cache），
                    # 缓存无此条时回退到创建时落盘的 local_bars.json（旧任务）。
                    # symbol_info 等合约参数仍取自网关快照。
                    rows = await self._load_cache_bars(task.symbol, task.timeframe)
                    if rows is None:
                        bars_file = payload_dir / "local_bars.json"
                        rows = json.loads(bars_file.read_text(encoding="utf-8"))
                    fetched = bars_from_local(
                        rows, task.start_date, task.end_date,
                        task.timeframe, task.warmup_bars,
                    )
                else:
                    fetched = await fetch_bars(
                        client, task.symbol, task.timeframe,
                        task.start_date, task.end_date, task.warmup_bars,
                    )
            except (GatewayError, DataUnavailableError) as exc:
                detail = getattr(exc, "detail", None) or str(exc)
                await self._safe_fail(task_id, "UNKNOWN_ERROR", f"取数失败：{detail}")
                return
            except (OSError, ValueError) as exc:
                await self._safe_fail(
                    task_id, "UNKNOWN_ERROR", f"本地数据文件读取失败：{exc}"
                )
                return

            # 3. payload + frozen config snapshot completion
            payload = self._build_payload(task, info, fetched, source)
            try:
                save_payload(payload, fetched.arrays, payload_dir)
            except (OSError, ValueError) as exc:
                logger.exception("payload write failed for %s", task_id)
                await self._safe_fail(task_id, "UNKNOWN_ERROR", f"任务数据写入失败：{exc}")
                return
            async with AsyncSessionLocal() as db:
                snap = dict(task.config_snapshot or {})
                snap["symbol_info"] = payload.symbol_info
                snap["spread_snapshot"] = payload.spread_snapshot
                snap["data_fingerprint"] = fetched.fingerprint
                snap["data_source"] = task.data_source
                snap["seed"] = payload.seed
                snap["warmup_actual"] = fetched.warmup_bars
                snap["swap_long"] = payload.swap_long
                snap["swap_short"] = payload.swap_short
                task.config_snapshot = snap
                db.add(task)
                await db.commit()
                # User may have cancelled during the fetch phase.
                live_status = await db.scalar(
                    select(BacktestTask.status).where(BacktestTask.id == uid)
                )
            if live_status != "running":
                _cleanup(payload_dir)
                if live_status == "cancelling":
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(BacktestTask)
                            .where(BacktestTask.id == uid,
                                   BacktestTask.status == "cancelling")
                            .values(status="cancelled", completed_at=datetime.now(UTC))
                        )
                        await db.commit()
                return
        finally:
            await client.aclose()

        # 4. spawn + stream + persist
        sup = RunnerSupervisor(task_id_str, payload_dir)
        self._supervisors[task_id_str] = sup
        try:
            state = _StreamState()
            result = await sup.run(self._make_on_event(task_id_str, state))
        finally:
            self._supervisors.pop(task_id_str, None)

        await self._finalize(task_id_str, result, state)

    # ── compile step ───────────────────────────────────────────

    def _compile(self, task: BacktestTask) -> tuple[str, Optional[list[str]]]:
        """AST validation + byte-compile; returns (source, error list or None)."""
        source = task.source_code
        vr = validate_source(source)
        if not vr.ok:
            return source, [f"[{i.code}] L{i.line}: {i.message}" for i in vr.issues]
        try:
            compile(source, "<strategy>", "exec")
        except SyntaxError as exc:
            return source, [f"语法错误：{exc}"]
        return source, None

    async def _write_version_compile(
        self, db, task: BacktestTask, errors: Optional[list[str]]
    ) -> None:
        if task.strategy_version is None:
            return
        row = await db.execute(
            select(StrategyVersion).where(
                StrategyVersion.strategy_id == task.strategy_id,
                StrategyVersion.version == task.strategy_version,
            )
        )
        ver = row.scalar_one_or_none()
        if ver is not None:
            ver.compiled_at = datetime.now(UTC)
            ver.compile_errors = errors
            db.add(ver)

    # ── payload assembly ───────────────────────────────────────

    def _build_payload(
        self, task: BacktestTask, info: dict, fetched, source: str
    ) -> BacktestPayload:
        trade_mode = _TRADE_MODE_MAP.get(int(info.get("trade_mode", 4) or 4), "full")
        symbol_info = {
            "display_name": info.get("description") or task.symbol,
            "category": info.get("path") or "",
            "base_currency": info.get("currency_base") or "USD",
            "quote_currency": info.get("currency_profit") or "USD",
            "digits": int(info.get("digits", 5)),
            "point": float(info.get("point", 0.00001)),
            "volume_step": float(info.get("volume_step", 0.01)),
            "volume_min": float(info.get("volume_min", 0.01)),
            "volume_max": float(info.get("volume_max", 100.0)),
            "contract_size": float(info.get("trade_contract_size", 100000.0)),
            "margin_initial": float(info.get("margin_initial", 0.0)),
            "trade_mode": trade_mode,
        }
        seed = int((task.config_snapshot or {}).get("seed") or random.randrange(2**31))
        return BacktestPayload(
            task_id=str(task.id),
            strategy_id=str(task.strategy_id),
            source_code=source,
            execution_mode=task.execution_mode,
            symbol=task.symbol,
            timeframe=task.timeframe,
            tf_seconds=TF_SECONDS[task.timeframe],
            initial_deposit=float(task.initial_deposit),
            leverage=task.leverage,
            spread_mode=task.spread_mode,
            spread_points=float(task.spread_points) if task.spread_points is not None else None,
            spread_snapshot=float(info.get("spread", 0) or 0),
            swap_long=float(info.get("swap_long", 0) or 0),
            swap_short=float(info.get("swap_short", 0) or 0),
            commission_per_lot=float(task.commission_per_lot),
            slippage_points=(
                float(task.slippage_points) if task.slippage_points is not None else None
            ),
            warmup_bars=fetched.warmup_bars,
            parameters=dict(task.parameters or {}),
            seed=seed,
            symbol_info=symbol_info,
            config_snapshot=dict(task.config_snapshot or {}),
            callback_timeout_seconds=float(settings.SANDBOX_CALLBACK_TIMEOUT_BACKTEST),
            on_init_timeout_seconds=float(settings.SANDBOX_ON_INIT_TIMEOUT),
            stop_out_level=settings.BACKTEST_STOP_OUT_LEVEL,
            log_limit=settings.BACKTEST_LOG_LIMIT,
        )

    # ── event persistence ──────────────────────────────────────

    def _make_on_event(self, task_id: str, state: "_StreamState"):
        async def on_event(event: dict) -> None:
            kind = event.get("event")
            if kind == "log":
                state.logs.append(event)
                state.log_seq += 1
            elif kind == "trade":
                state.trades.append(event)
            elif kind == "progress":
                pct = int(event.get("percent", 0))
                state.last_progress = max(state.last_progress, pct)
                now = time.monotonic()
                if pct >= 100 or now - state.last_progress_write >= _PROGRESS_MIN_INTERVAL:
                    state.last_progress_write = now
                    await self._persist_progress(task_id, pct)
            elif kind == "equity":
                state.equity_curve = list(event.get("curve") or [])
            elif kind == "done":
                state.report = dict(event.get("report") or {})
            elif kind == "fail":
                state.fail = dict(event)
            if len(state.logs) + len(state.trades) >= _FLUSH_ROWS:
                await self._flush_rows(task_id, state)
        return on_event

    async def _persist_progress(self, task_id: str, pct: int) -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(BacktestTask)
                .where(BacktestTask.id == _uuid(task_id), BacktestTask.status == "running")
                .values(progress_percent=pct)
            )
            await db.commit()

    async def _flush_rows(self, task_id: str, state: "_StreamState") -> None:
        uid = _uuid(task_id)
        async with AsyncSessionLocal() as db:
            for ev in state.logs:
                db.add(BacktestLog(
                    task_id=uid,
                    seq=int(ev.get("seq", 0)),
                    bar_time=_dt(ev.get("bar_time")),
                    kind=str(ev.get("kind", "log")),
                    level=str(ev.get("level", "info")),
                    message=str(ev.get("message", ""))[:2000],
                    payload=ev.get("payload"),
                ))
            for ev in state.trades:
                t = ev  # trade dict at top level (runner emits {"event":"trade", **trade})
                db.add(BacktestTrade(
                    task_id=uid,
                    ticket=int(t["ticket"]),
                    direction=str(t["direction"]),
                    volume=_dec(t["volume"], 4),
                    open_time=_dt(t["open_time"]) or datetime(1970, 1, 1, tzinfo=UTC),
                    open_price=_dec(t["open_price"]),
                    base_price=_dec(t["base_price"]),
                    close_time=_dt(t["close_time"]),
                    close_price=(
                        _dec(t["close_price"]) if t.get("close_price") is not None else None
                    ),
                    profit=_dec(t["profit"], 4),
                    profit_percent=_dec(t["profit_percent"], 6),
                    swap=_dec(t.get("swap", 0.0), 4),
                    commission=_dec(t.get("commission", 0.0), 4),
                    spread_cost=_dec(t.get("spread_cost", 0.0)),
                    slippage_cost=_dec(t.get("slippage_cost", 0.0)),
                    comment=str(t.get("comment", ""))[:200],
                    is_forced=bool(t.get("is_forced", False)),
                ))
            state.logs.clear()
            state.trades.clear()
            await db.commit()

    # ── terminal handling ──────────────────────────────────────

    async def _finalize(self, task_id: str, result: SuperviseResult, state: "_StreamState") -> None:
        await self._flush_rows(task_id, state)
        uid = _uuid(task_id)
        now = datetime.now(UTC)
        async with AsyncSessionLocal() as db:
            task = await db.get(BacktestTask, uid)
            if task is None:
                return
            if result.outcome == TERM_CANCELLED:
                await db.execute(
                    update(BacktestTask)
                    .where(BacktestTask.id == uid,
                           BacktestTask.status.in_(["running", "cancelling"]))
                    .values(status="cancelled", completed_at=now)
                )
                await db.commit()
                _cleanup(Path(settings.BACKTEST_RUNTIME_DIR) / task_id)
                return
            if result.outcome == TERM_TIMEOUT:
                await self._finish_failed(
                    db, task, "RESOURCE_TIMEOUT",
                    f"任务超过 {settings.BACKTEST_TIMEOUT_SECONDS}s 总时限被终止",
                    timed_out=True,
                )
                _cleanup(Path(settings.BACKTEST_RUNTIME_DIR) / task_id)
                return
            if result.outcome == TERM_RESOURCE:
                await self._finish_failed(
                    db, task, "RESOURCE_EXCEEDED",
                    f"子进程内存超过 {settings.SANDBOX_MAX_MEMORY_MB}MB 上限被终止",
                )
                _cleanup(Path(settings.BACKTEST_RUNTIME_DIR) / task_id)
                return
            if result.outcome == TERM_CRASH:
                await self._finish_failed(
                    db, task, "UNKNOWN_ERROR",
                    f"沙箱进程异常退出（code={result.exit_code}）"
                    + (f"：{result.stderr_tail[-500:]}" if result.stderr_tail else ""),
                )
                _cleanup(Path(settings.BACKTEST_RUNTIME_DIR) / task_id)
                return
            if result.outcome == TERM_FAILED or state.fail is not None:
                fail = state.fail or {}
                msg = str(fail.get("message") or "沙箱安全终止")
                tb = str(fail.get("traceback") or "")
                await self._finish_failed(
                    db, task, str(fail.get("code") or "SANDBOX_VIOLATION"),
                    msg + (f"\n{tb[-1500:]}" if tb else ""),
                )
                _cleanup(Path(settings.BACKTEST_RUNTIME_DIR) / task_id)
                return
            # TERM_COMPLETED — write report
            if state.report is None:
                await self._finish_failed(
                    db, task, "UNKNOWN_ERROR", "沙箱正常退出但未产出报告"
                )
                _cleanup(Path(settings.BACKTEST_RUNTIME_DIR) / task_id)
                return
            r = state.report
            db.add(BacktestReport(
                task_id=uid,
                total_return=_dec(r.get("total_return", 0.0), 4),
                total_return_percent=_dec(r.get("total_return_percent", 0.0), 6),
                annual_return_percent=_dec(r.get("annual_return_percent", 0.0), 6),
                annual_volatility_percent=_dec(r.get("annual_volatility_percent", 0.0), 6),
                sharpe_ratio=_dec(r.get("sharpe_ratio", 0.0), 4),
                sortino_ratio=_dec(r.get("sortino_ratio", 0.0), 4),
                calmar_ratio=_dec(r.get("calmar_ratio", 0.0), 4),
                recovery_factor=_dec(r.get("recovery_factor", 0.0), 4),
                max_drawdown_percent=_dec(r.get("max_drawdown_percent", 0.0), 6),
                max_drawdown_amount=_dec(r.get("max_drawdown_amount", 0.0), 4),
                win_rate=_dec(r.get("win_rate", 0.0), 6),
                profit_factor=_dec(r.get("profit_factor", 0.0), 4),
                expectancy=_dec(r.get("expectancy", 0.0), 6),
                total_trades=int(r.get("total_trades", 0)),
                winning_trades=int(r.get("winning_trades", 0)),
                losing_trades=int(r.get("losing_trades", 0)),
                avg_win=_dec(r.get("avg_win", 0.0), 4),
                avg_loss=_dec(r.get("avg_loss", 0.0), 4),
                avg_hold_hours=_dec(r.get("avg_hold_hours", 0.0), 4),
                consecutive_wins=int(r.get("consecutive_wins", 0)),
                consecutive_losses=int(r.get("consecutive_losses", 0)),
                total_swap=_dec(r.get("total_swap", 0.0), 4),
                equity_curve=state.equity_curve,
                monthly_returns=dict(r.get("monthly_returns") or {}),
            ))
            snap = dict(task.config_snapshot or {})
            snap["n_bars"] = int(r.get("n_bars", 0))
            snap["log_dropped"] = int(r.get("log_dropped", 0))
            task.config_snapshot = snap
            task.status = "completed"
            task.progress_percent = 100
            task.completed_at = now
            db.add(task)
            await db.commit()
        _cleanup(Path(settings.BACKTEST_RUNTIME_DIR) / task_id)

    async def _finish_failed(
        self, db, task: BacktestTask, code: str, message: str, timed_out: bool = False
    ) -> None:
        task.status = "failed"
        task.error_code = code[:32]
        task.error_message = message[:8000]
        task.timed_out = timed_out
        task.completed_at = datetime.now(UTC)
        db.add(task)
        await db.commit()
        # P0-4 结构化故障输出：回测失败收尾
        ft = FaultType.BACKTEST_FAILED
        if timed_out or code in ("RESOURCE_TIMEOUT", "TIMED_OUT"):
            ft = FaultType.STRATEGY_TIMEOUT
        elif code == "CANCELLED":
            ft = FaultType.STARTUP_FAILED
        elif code in ("ILLEGAL_IMPORT", "SANDBOX_VIOLATION"):
            ft = FaultType.STRATEGY_ILLEGAL_IMPORT
        elif code in ("DATA_UNAVAILABLE", "NO_DATA", "DATA_FETCH"):
            ft = FaultType.DATA_FEED_STALE
        await gate_fault(
            fault_type=ft,
            action_taken=f"回测失败（code={code}, timed_out={timed_out}）",
            run_id=task.id,
            detail=message,
        ).emit(db=db)

    async def _load_cache_bars(self, symbol: str, timeframe: str) -> list[dict] | None:
        """Read bars from the shared kline_cache table; None if absent.

        缓存按模拟/实盘隔离，同名品种可能两行并存：虚拟下单优先（回测主场景）。
        """
        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(KlineCache)
                    .where(
                        KlineCache.symbol == symbol.upper(),
                        KlineCache.timeframe == timeframe,
                    )
                    .order_by(KlineCache.account_type.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return list(row.bars) if row is not None else None

    async def _safe_fail(self, task_id: str, code: str, message: str) -> None:
        async with AsyncSessionLocal() as db:
            task = await db.get(BacktestTask, _uuid(task_id))
            if task is not None and task.status in ("pending", "running", "cancelling"):
                await self._finish_failed(db, task, code, message)


class _StreamState:
    """Mutable per-run accumulation for event persistence."""

    __slots__ = (
        "logs", "trades", "last_progress", "last_progress_write",
        "equity_curve", "report", "fail", "log_seq",
    )

    def __init__(self) -> None:
        self.logs: list[dict] = []
        self.trades: list[dict] = []
        self.last_progress = 0
        self.last_progress_write = 0.0
        self.equity_curve: list[dict] = []
        self.report: Optional[dict] = None
        self.fail: Optional[dict] = None
        self.log_seq = 0


def _cleanup(directory: Path) -> None:
    shutil.rmtree(directory, ignore_errors=True)


# ── singleton ──────────────────────────────────────────────────
_queue_singleton: Optional[BacktestQueue] = None


def get_backtest_queue() -> BacktestQueue:
    global _queue_singleton
    if _queue_singleton is None:
        _queue_singleton = BacktestQueue()
    return _queue_singleton



