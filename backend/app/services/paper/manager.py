"""Paper-trading manager (M5) — in-process singleton orchestrator.

One :class:`PaperManager` owns every live paper run.  Each run is a long-lived
sandbox subprocess (``app.sandbox.paper_runner``, same matching kernel as M4)
fed **newly closed bars** by a per-run gateway polling loop:

    start → symbol-info + history fetch (warmup + PAPER_HISTORY_BARS) →
    payload dir write → spawn supervisor → stream task persists JSONL events
    (log→paper_logs, trade→paper_trades, account→run-row mirror throttled to
    2s) → poll task delivers closed bars over stdin every PAPER_POLL_SECONDS.

Control semantics (PRD 运行控制三态):
    pause  = parent stops delivering bars (child blocks on stdin, state kept)
    resume = polling continues
    stop   = {"cmd":"stop","close":bool} → optional forced close at last
             close → final account snapshot → child exit 0 → status stopped
    reset  = stop + wipe trades/logs/storage + fresh history → start again

``startup_sweep`` marks orphaned running/paused/stopping rows failed after a
backend restart (the subprocesses die with the parent's stdin beacon).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid as uuid_mod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

import numpy as np
from sdk.errors import ErrorCode
from sqlalchemy import delete, select, update

from app.config import settings
from app.engine.session import AsyncSessionLocal
from app.models import PaperLog, PaperRun, PaperTrade
from app.sandbox.matching import TF_SECONDS
from app.sandbox.payload import BacktestPayload, BarArrays, save_payload
from app.services.backtest.supervisor import (
    TERM_COMPLETED,
    TERM_STALLED,
    RunnerSupervisor,
)
from app.services.ea_bridge import (
    registry as ea_registry,
)
from app.services.ea_bridge import (
    request_ea_rates,
)
from app.services.fault_state import (  # P0-4 统一故障状态机
    FaultType,
    gate_fault,
)
from app.services.gateway_client import (
    GatewayClient,
    GatewayError,
    GatewayLoginError,
)
from app.services.gateway_route import (
    account_credentials,
    resolve_gateway,
    resolve_node,
)
from app.services.run_base import BaseRunManager
from app.services.strategy_validator import has_callback
from app.services.tick_feed import fetch_ea_buffer_ticks, fetch_gateway_ticks

logger = logging.getLogger("mt5quant.paper.manager")

_FLUSH_ROWS = 200
_ACCOUNT_WRITE_INTERVAL = 2.0  # seconds between run-row mirror writes


def _uuid(s: str) -> uuid_mod.UUID:
    return uuid_mod.UUID(s)


def _dec(v: object, places: int = 8) -> Decimal:
    return Decimal(str(round(float(v), places)))  # type: ignore[arg-type]


def _dt(epoch_or_iso: object) -> Optional[datetime]:
    if epoch_or_iso is None:
        return None
    if isinstance(epoch_or_iso, (int, float)):
        return datetime.fromtimestamp(int(epoch_or_iso), tz=UTC)
    return datetime.fromisoformat(str(epoch_or_iso))


@dataclass
class _RunHandle:
    run_id: str
    sup: RunnerSupervisor
    stream_task: asyncio.Task
    poll_task: asyncio.Task
    dir: Path
    symbol: str
    timeframe: str
    tf_seconds: int
    gateway_url: str = ""  # M12: node resolved from the bound account at start
    node_type: str = "python"  # python | ea_bridge (P4 EB-1 tick feed branch)
    ea_node_id: str = ""  # registry key when node_type == "ea_bridge"
    paused: asyncio.Event = field(default_factory=asyncio.Event)
    ready: bool = False  # startup replay drained (first account event)
    stopping: bool = False
    last_ts: int = 0  # epoch of last delivered bar (poll cursor)
    # on_tick feed (SDK v1.1.0): in-memory cursor + child-live gate
    has_on_tick: bool = False
    tick_task: Optional[asyncio.Task] = None
    child_live: bool = False  # runner emitted "started" (replay done)
    last_tick_msc: int = 0  # ms cursor; advances only while sending
    # throttled account mirror state
    account: dict = field(default_factory=dict)
    pending_equity: list[dict] = field(default_factory=list)
    last_account_write: float = 0.0
    ea_account_sig: tuple = ()  # EA 镜像去重签名（balance/equity/margin_free/n_pos）
    logs: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    last_log_flush: float = 0.0  # 5s idle flush safety net (mirror live manager)
    fail: Optional[dict] = None


class PaperManager(BaseRunManager):
    async def startup_sweep(self) -> None:
        """Mark rows orphaned by a backend restart as failed."""
        now = datetime.now(UTC)
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(PaperRun)
                .where(PaperRun.status.in_(["running", "paused", "stopping"]))
                .values(
                    status="failed",
                    error_code="UNKNOWN_ERROR",
                    error_message="服务重启导致虚拟下单中断，请重置后重新启动",
                    stopped_at=now,
                )
            )
            await db.commit()

    async def shutdown(self) -> None:
        for rid in list(self._handles):
            h = self._handles.get(rid)
            if h is None:
                continue
            h.sup.request_cancel()
            for t in (h.stream_task, h.poll_task, h.tick_task):
                if t is not None:
                    t.cancel()
        deadline = time.monotonic() + 10.0
        while self._handles and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        self._handles.clear()
        logger.info("paper manager stopped")

    # active_count() inherited from BaseRunManager

    # ── start / spawn ──────────────────────────────────────────

    async def start(self, run_id: str) -> None:
        """Fetch history + spawn the paper_runner for a pending run row."""
        if run_id in self._handles:
            return
        uid = _uuid(run_id)
        async with AsyncSessionLocal() as db:
            run = await db.get(PaperRun, uid)
            if run is None or run.status != "pending":
                return
            res = await db.execute(
                update(PaperRun)
                .where(PaperRun.id == uid, PaperRun.status == "pending")
                .values(status="running", started_at=datetime.now(UTC))
            )
            await db.commit()
            if not res.rowcount:  # type: ignore[attr-defined]  # SQLAlchemy Result type annotation gap — real type has .rowcount/.scalars()
                return  # cancelled between create and claim
            run = await db.get(PaperRun, uid)
            assert run is not None
            payload_dir = Path(settings.PAPER_RUNTIME_DIR) / run_id
            # M12: route every gateway call to the node the bound account lives
            # on (MT5 single-terminal = one account per node).  Legacy rows with
            # no account fall back to the global default and skip login.
            gateway_url = await resolve_gateway(db, run.account_id)
            node = await resolve_node(db, run.account_id)
            node_type = node.type if node is not None else "python"
            ea_node_id = ""
            if node_type == "ea_bridge":
                assert node is not None, "ea_bridge node must exist when type matches"
                ea_node_id = str(node.id)
            creds = (await account_credentials(db, run.account_id)
                     if run.account_id is not None else None)

        if node_type == "ea_bridge":
            # P4 EB-2: EA 桥节点完整数据面 —— 启动快照走 rates RPC。
            # 「最新 N 根」语义（禁固定日历窗）：休市/周末固定回看窗口会整段
            # 落入无数据区间；EA 侧 CopyRates 按根数取天然落在最近 bar 上。
            info, rows, tf, need = await self._fetch_ea_startup(
                run_id, ea_node_id, run,
            )
            if rows is None:
                return  # fail-fast 已落库
        else:
            client = GatewayClient.for_registered_node(gateway_url)
            try:
                if creds is not None:
                    login, password, server = creds
                    try:
                        await client.login(login, password, server)
                    except (GatewayLoginError, GatewayError) as exc:
                        await self._fail(
                            run_id, "UNKNOWN_ERROR",
                            f"网关登录绑定账户失败（{login}@{server}）：{exc.detail}",
                        )
                        return
                elif run.account_id is not None:
                    await self._fail(
                        run_id, "UNKNOWN_ERROR",
                        "绑定账户缺少可用凭据（密码密文缺失或解密失败），"
                        "请重新绑定账户后再启动虚拟下单",
                    )
                    return
                try:
                    symbols = await client.symbols()
                except GatewayError as exc:
                    await self._fail(run_id, "UNKNOWN_ERROR", f"网关不可达：{exc.detail}")
                    return
                sym_info: dict | None = next(
                    (s for s in symbols if s.get("name", "").upper() == run.symbol.upper()),
                    None,
                )
                if sym_info is None:
                    await self._fail(run_id, "INVALID_SYMBOL", f"网关不存在品种：{run.symbol}")
                    return
                info = sym_info
                tf = TF_SECONDS[run.timeframe]
                need = run.warmup_bars + settings.PAPER_HISTORY_BARS + 1
                # 用「最新 N 根」端点而非日历时间窗口：休市/周末期间固定回看窗口
                # 会整段落入无数据区间，网关该端点内部会回退到最近 N 根。
                try:
                    rows = await client.rates(run.symbol, run.timeframe, min(need, 5000))
                except GatewayError as exc:
                    await self._fail(run_id, "UNKNOWN_ERROR", f"取数失败：{exc.detail}")
                    return
            finally:
                await client.aclose()

        # 最新 N 根可能含尚未收盘的实时 bar；只保留已收盘 bar，
        # 与 poll 的 time+tf<=now 语义对齐，避免末根被跳过或提前成交。
        rows = _select_history(rows, tf, int(time.time()), need)
        if not rows:
            await self._fail(
                run_id, "UNKNOWN_ERROR",
                f"网关无 {run.symbol} {run.timeframe} 历史数据，无法启动虚拟下单",
            )
            return
        arrays = _rows_to_arrays(rows)
        payload = self._build_payload(
            run, info, float(info.get("spread", 0) or 0),
            has_on_tick=has_callback(run.source_code, "on_tick"),
        )
        try:
            save_payload(payload, arrays, payload_dir)
        except (OSError, ValueError) as exc:
            await self._fail(run_id, "UNKNOWN_ERROR", f"任务数据写入失败：{exc}")
            return
        async with AsyncSessionLocal() as db:
            snap = dict(run.config_snapshot or {})
            snap["symbol_info"] = payload.symbol_info
            snap["spread_snapshot"] = payload.spread_snapshot
            snap["history_bars"] = len(arrays)
            run.config_snapshot = snap
            db.add(run)
            await db.commit()

        h = _RunHandle(
            run_id=run_id,
            sup=RunnerSupervisor(
                run_id, payload_dir, module="app.sandbox.paper_runner",
                stall_timeout=float(settings.SANDBOX_STALL_TIMEOUT_SECONDS),
            ),
            stream_task=asyncio.create_task(
                self._stream(run_id), name=f"paper-stream-{run_id[:8]}"
            ),
            poll_task=asyncio.create_task(
                self._poll(run_id), name=f"paper-poll-{run_id[:8]}"
            ),
            dir=payload_dir,
            symbol=run.symbol,
            timeframe=run.timeframe,
            tf_seconds=tf,
            gateway_url=gateway_url,
            node_type=node_type,
            ea_node_id=ea_node_id,
            last_ts=int(rows[-1]["time"]),
        )
        if payload.has_on_tick:
            h.has_on_tick = True
            h.last_tick_msc = int(time.time() * 1000)
        h.paused.set()  # paused until the startup replay drains (stream sets)
        self._handles[run_id] = h
        if payload.has_on_tick:
            h.tick_task = asyncio.create_task(
                self._tick_poll(run_id), name=f"paper-tick-{run_id[:8]}"
            )
        logger.info(
            "paper run %s spawned (%d history bars%s)",
            run_id, len(rows), ", on_tick" if h.has_on_tick else "",
        )

    async def _fetch_ea_startup(
        self, run_id: str, ea_node_id: str, run: PaperRun,
    ) -> tuple[dict, list[dict] | None, int, int]:
        """EA 桥启动快照：rates RPC 拉最新 N 根 → (symbol_info, rows, tf, need)。

        rows 为 None 表示已 fail-fast 落库（EA 未上线 / 超时 / 应答异常 /
        无数据），start() 直接返回。bars 行结构与 python 网关 rates 同域，
        共享尾部（_select_history → arrays → payload → spawn）两端一致。
        """
        if ea_registry.last_seen(ea_node_id) <= 0:
            await self._fail(
                run_id, "GATEWAY_OFFLINE",
                "EA 桥节点未上线（终端未挂 EA 或尚未完成配对同步），无法启动虚拟下单",
            )
            return {}, None, 0, 0
        tf = TF_SECONDS[run.timeframe]
        need = run.warmup_bars + settings.PAPER_HISTORY_BARS + 1
        res = await request_ea_rates(ea_node_id, run.symbol, run.timeframe, count=need)
        if not res["ok"]:
            err = str(res.get("error") or "UNKNOWN")
            if err == "RESULT_TIMEOUT":
                await self._fail(
                    run_id, "GATEWAY_TIMEOUT",
                    "EA 桥取数超时（EA 未在时限内应答 rates 请求，请确认终端在线）",
                )
            else:
                await self._fail(run_id, "UNKNOWN_ERROR", f"EA 桥取数失败：{err}")
            return {}, None, tf, need
        payload = res.get("payload") or {}
        info = payload.get("symbol_info") or {}
        if not info:
            await self._fail(
                run_id, "UNKNOWN_ERROR",
                "EA 桥未返回品种信息（EA 版本过旧，请更新 EAQuantBridge 后重试）",
            )
            return {}, None, tf, need
        rows = _ea_bars_to_rows(payload.get("bars") or [])
        if not rows:
            await self._fail(
                run_id, "UNKNOWN_ERROR",
                f"EA 桥无 {run.symbol} {run.timeframe} 历史数据，无法启动虚拟下单",
            )
            return {}, None, tf, need
        return info, rows, tf, need

    def _build_payload(
        self, run: PaperRun, info: dict, spread_snapshot: float,
        *, has_on_tick: bool = False,
    ) -> BacktestPayload:
        from app.services.backtest.queue import _TRADE_MODE_MAP

        trade_mode = _TRADE_MODE_MAP.get(int(info.get("trade_mode", 4) or 4), "full")
        symbol_info = {
            "display_name": info.get("description") or run.symbol,
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
        snap = run.config_snapshot or {}
        return BacktestPayload(
            task_id=str(run.id),
            strategy_id=str(run.strategy_id),
            source_code=run.source_code,
            execution_mode=run.execution_mode,
            symbol=run.symbol,
            timeframe=run.timeframe,
            tf_seconds=TF_SECONDS[run.timeframe],
            initial_deposit=float(run.initial_deposit),
            leverage=run.leverage,
            spread_mode=run.spread_mode,
            spread_points=float(run.spread_points) if run.spread_points is not None else None,
            spread_snapshot=spread_snapshot,
            swap_long=float(info.get("swap_long", 0) or 0),
            swap_short=float(info.get("swap_short", 0) or 0),
            commission_per_lot=float(run.commission_per_lot),
            slippage_points=(
                float(run.slippage_points) if run.slippage_points is not None else None
            ),
            warmup_bars=run.warmup_bars,
            parameters=dict(run.parameters or {}),
            seed=int(snap.get("seed") or 0),
            symbol_info=symbol_info,
            config_snapshot=dict(snap),
            callback_timeout_seconds=float(settings.SANDBOX_CALLBACK_TIMEOUT_LIVE),
            on_init_timeout_seconds=float(settings.SANDBOX_ON_INIT_TIMEOUT),
            stop_out_level=settings.PAPER_STOP_OUT_LEVEL,
            log_limit=settings.PAPER_LOG_LIMIT,
            has_on_tick=has_on_tick,
        )

    # ── stream task (event persistence) ────────────────────────

    async def _stream(self, run_id: str) -> None:
        h = self._handles.get(run_id)
        assert h is not None
        try:
            result = await h.sup.run(self._on_event(run_id), timeout=None)
        except Exception:  # noqa: BLE001
            logger.exception("paper stream %s crashed", run_id)
            await self._fail(run_id, "UNKNOWN_ERROR", "虚拟下单编排异常，请重置后重新启动")
            self._handles.pop(run_id, None)
            return

        await self._flush_rows(run_id)
        await self._write_account(run_id, force=True)
        uid = _uuid(run_id)
        now = datetime.now(UTC)
        async with AsyncSessionLocal() as db:
            run = await db.get(PaperRun, uid)
            if run is None:
                self._handles.pop(run_id, None)
                return
            if result.outcome == TERM_COMPLETED:
                run.status = "stopped"
                run.stopped_at = now
            elif result.outcome == TERM_STALLED:
                # 卡死杀进程时不会有 fail 事件——父侧自己得把原因说清楚，
                # 否则用户只看得到「沙箱进程异常退出」。
                run.status = "failed"
                run.stopped_at = now
                run.error_code = ErrorCode.RESOURCE_TIMEOUT.value
                run.error_message = (
                    f"策略进程连续 {settings.SANDBOX_STALL_TIMEOUT_SECONDS} 秒无任何响应"
                    f"（含心跳），已判定卡死并终止；常见原因是回调中的死循环或长时间阻塞。"
                    f"\n{result.stderr_tail[-1500:]}"
                )[:8000]
                # P0-4 结构化故障输出：模拟策略卡死
                await gate_fault(
                    fault_type=FaultType.STRATEGY_TIMEOUT,
                    action_taken="判定卡死并终止沙箱进程",
                    run_id=run.id,
                    detail=run.error_message,
                ).emit(db=db)
            else:
                fail = h.fail or {}
                run.status = "failed"
                run.stopped_at = now
                run.error_code = str(fail.get("code") or "UNKNOWN_ERROR")[:32]
                msg = str(fail.get("message") or "沙箱进程异常退出")
                tb = str(fail.get("traceback") or "")
                run.error_message = (msg + (f"\n{tb[-1500:]}" if tb else ""))[:8000]
                # P0-4 结构化故障输出：模拟策略崩溃 / 非法 import / 其他异常
                ft = FaultType.STRATEGY_CRASHED
                if run.error_code in ("ILLEGAL_IMPORT", "SANDBOX_VIOLATION"):
                    ft = FaultType.STRATEGY_ILLEGAL_IMPORT
                await gate_fault(
                    fault_type=ft,
                    action_taken=f"策略进程异常退出（code={run.error_code}）",
                    run_id=run.id,
                    detail=run.error_message,
                ).emit(db=db)
            db.add(run)
            await db.commit()
        if result.outcome != TERM_COMPLETED:
            shutil.rmtree(h.dir, ignore_errors=True)
        self._handles.pop(run_id, None)

    def _on_event(self, run_id: str):
        async def on_event(event: dict) -> None:
            h = self._handles.get(run_id)
            if h is None:
                return
            kind = event.get("event")
            if kind == "log":
                h.logs.append(event)
            elif kind == "trade":
                h.trades.append(event)
            elif kind == "account":
                if not h.ready:
                    # startup replay drained — allow bar delivery from now on;
                    # after this, paused is owned solely by pause()/resume()
                    h.ready = True
                    h.paused.clear()
                if h.node_type == "ea_bridge":
                    # EA 桥 run：run 行账户由 EA sync 镜像拥有（live 同构，
                    # EB-3 起订单在真实 demo 账户执行）；沙箱模拟账户仅用于
                    # 信号回放，不写 run 行。
                    if ts := event.get("last_bar_time"):
                        if dt := _dt(ts):
                            h.last_ts = max(h.last_ts, int(dt.timestamp()))
                else:
                    h.account = event  # _write_account 的镜像源（balance/equity/positions）
                    ts = event.get("last_bar_time")
                    if ts:
                        if dt := _dt(ts):
                            h.last_ts = max(h.last_ts, int(dt.timestamp()))
                    h.pending_equity.append({
                        "time": ts,
                        "equity": float(event.get("equity", 0.0)),
                        "balance": float(event.get("balance", 0.0)),
                    })
                    if time.monotonic() - h.last_account_write >= _ACCOUNT_WRITE_INTERVAL:
                        await self._write_account(run_id)
            elif kind == "started":
                h.child_live = True  # replay done — ticks may flow
            elif kind == "fail":
                h.fail = dict(event)
            if len(h.logs) + len(h.trades) >= _FLUSH_ROWS:
                await self._flush_rows(run_id)
                h.last_log_flush = time.monotonic()
            elif h.logs and time.monotonic() - h.last_log_flush >= 5.0:
                # idle-market safety net: don't hold low-volume lines forever
                await self._flush_rows(run_id)
                h.last_log_flush = time.monotonic()
        return on_event

    async def _write_account(self, run_id: str, force: bool = False) -> None:
        h = self._handles.get(run_id)
        if h is None or not h.account:
            return
        a = h.account
        uid = _uuid(run_id)
        async with AsyncSessionLocal() as db:
            run = await db.get(PaperRun, uid)
            if run is None or run.status in ("stopped", "failed"):
                return
            eq_hist = list(run.equity_history or [])
            eq_hist.extend(h.pending_equity)
            if len(eq_hist) > settings.PAPER_EQUITY_POINTS_MAX:
                eq_hist = eq_hist[-settings.PAPER_EQUITY_POINTS_MAX:]
            run.balance = _dec(a.get("balance", 0.0), 4)
            run.equity = _dec(a.get("equity", 0.0), 4)
            run.margin_used = _dec(a.get("margin_used", 0.0), 4)
            run.margin_free = _dec(a.get("margin_free", 0.0), 4)
            run.unrealized_pnl = _dec(a.get("unrealized_pnl", 0.0), 4)
            run.positions = list(a.get("positions") or [])
            run.last_bar_time = _dt(a.get("last_bar_time"))
            run.equity_history = eq_hist
            db.add(run)
            await db.commit()
        h.pending_equity.clear()
        h.last_account_write = time.monotonic()

    async def _mirror_ea_account(self, run_id: str) -> None:
        """EA runs: registry.last_account → run-row mirror（_write_account 同构）。

        EA 每次 sync 上行真实 demo 账户快照（EB-3 起订单也在该账户执行）；
        签名去重避免每轮 poll 空写。字段映射与 runner account 事件逐字段
        同构（防漏赋值坑），last_bar_time 由后端 poll 游标注入。
        """
        h = self._handles.get(run_id)
        if h is None or h.node_type != "ea_bridge" or h.stopping:
            return
        raw = ea_registry.last_account(h.ea_node_id)
        if not raw:
            return
        try:
            sig = (
                round(float(raw.get("balance", 0.0)), 2),
                round(float(raw.get("equity", 0.0)), 2),
                round(float(raw.get("margin_free", 0.0)), 2),
                len(raw.get("positions") or []),
            )
        except (TypeError, ValueError):
            return
        if sig == h.ea_account_sig:
            return
        h.ea_account_sig = sig
        h.account = _ea_account_event(raw, h.last_ts)
        h.pending_equity.append({
            "time": h.account["last_bar_time"],
            "equity": h.account["equity"],
            "balance": h.account["balance"],
        })
        await self._write_account(run_id)

    async def _flush_rows(self, run_id: str) -> None:
        h = self._handles.get(run_id)
        if h is None or (not h.logs and not h.trades):
            return
        uid = _uuid(run_id)
        async with AsyncSessionLocal() as db:
            for ev in h.logs:
                db.add(PaperLog(
                    run_id=uid,
                    seq=int(ev.get("seq", 0)),
                    bar_time=_dt(ev.get("bar_time")),
                    kind=str(ev.get("kind", "log")),
                    level=str(ev.get("level", "info")),
                    message=str(ev.get("message", ""))[:2000],
                    payload=ev.get("payload"),
                ))
            for t in h.trades:
                db.add(PaperTrade(
                    run_id=uid,
                    ticket=int(t["ticket"]),
                    direction=str(t["direction"]),
                    volume=_dec(t["volume"], 4),
                    open_time=_dt(t["open_time"]) or datetime(1970, 1, 1, tzinfo=UTC),
                    open_price=_dec(t["open_price"]),
                    base_price=_dec(t["base_price"]),
                    close_time=_dt(t["close_time"]) or datetime(1970, 1, 1, tzinfo=UTC),
                    close_price=_dec(t["close_price"]),
                    profit=_dec(t["profit"], 4),
                    profit_percent=_dec(t["profit_percent"], 6),
                    swap=_dec(t.get("swap", 0.0), 4),
                    commission=_dec(t.get("commission", 0.0), 4),
                    spread_cost=_dec(t.get("spread_cost", 0.0)),
                    slippage_cost=_dec(t.get("slippage_cost", 0.0)),
                    comment=str(t.get("comment", ""))[:200],
                    is_forced=bool(t.get("is_forced", False)),
                    # 引擎在熔断判定时打上的标记（见 engine._quarantine）。
                    suspect=bool(t.get("suspect", False)),
                ))
            h.logs.clear()
            h.trades.clear()
            await db.commit()

    # ── poll task (closed-bar delivery) ────────────────────────

    async def _poll(self, run_id: str) -> None:
        h = self._handles.get(run_id)
        assert h is not None
        is_ea = h.node_type == "ea_bridge"
        client = None if is_ea else GatewayClient.for_registered_node(h.gateway_url or None)
        try:
            while True:
                await asyncio.sleep(settings.PAPER_POLL_SECONDS)
                cur = self._handles.get(run_id)
                if cur is not h:
                    return  # handle replaced/removed
                if h.paused.is_set() or h.stopping:
                    continue
                now = int(time.time())
                # a bar is CLOSED once its period has fully elapsed
                end = now - h.tf_seconds + 1
                if end <= h.last_ts:
                    continue
                try:
                    if is_ea:
                        # EA 桥：账户镜像搭车 poll 节奏；补数走 rates RPC
                        # （[last_ts+1, end) 区间），超时/掉线降频重试——
                        # 游标不推进，EA 恢复后区间自动补齐。
                        await self._mirror_ea_account(run_id)
                        res = await request_ea_rates(
                            h.ea_node_id, h.symbol, h.timeframe,
                            start=h.last_ts + 1, end=end,
                        )
                        if not res["ok"]:
                            logger.warning(
                                "paper %s ea poll failed: %s",
                                run_id, res.get("error"),
                            )
                            continue
                        rows = _ea_bars_to_rows(
                            (res.get("payload") or {}).get("bars") or [],
                        )
                    else:
                        assert client is not None, "client must exist when not is_ea"
                        rows = await client.rates_range(
                            h.symbol, h.timeframe, h.last_ts + 1, end,
                        )
                except GatewayError as exc:
                    logger.warning("paper %s poll failed: %s", run_id, exc.detail)
                    continue
                fresh = [
                    [r["time"], r["open"], r["high"], r["low"], r["close"],
                     r.get("tick_volume", 0), r.get("spread", 0)]
                    for r in rows
                    if int(r["time"]) > h.last_ts and int(r["time"]) + h.tf_seconds <= now
                ]
                if not fresh:
                    continue
                ok = await h.sup.send({"cmd": "bars", "rows": fresh})
                if not ok:
                    return  # child gone — stream task finalises
                h.last_ts = max(int(r[0]) for r in fresh)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("paper poll %s crashed", run_id)
        finally:
            if client is not None:  # EA 分支无网关客户端
                await client.aclose()

    # ── tick poll task (on_tick feed, SDK v1.1.0) ──────────────

    async def _tick_poll(self, run_id: str) -> None:
        h = self._handles.get(run_id)
        assert h is not None
        is_ea = h.node_type == "ea_bridge"
        client = None if is_ea else GatewayClient.for_registered_node(h.gateway_url or None)
        try:
            while True:
                await asyncio.sleep(settings.TICK_POLL_SECONDS)
                cur = self._handles.get(run_id)
                if cur is not h:
                    return  # handle replaced/removed
                if not h.child_live or h.paused.is_set() or h.stopping:
                    continue  # warmup replay / paused / stopping → no ticks
                if not is_ea:
                    now_s = int(time.time())
                    start_s = h.last_tick_msc // 1000
                    if now_s <= start_s:
                        continue  # same second — nothing new to fetch
                try:
                    if is_ea:
                        # EA 桥节点：读注册表缓冲（EA sync 推入），
                        # 去重/跳过仍由 last_tick_msc 游标兜底。
                        raw = fetch_ea_buffer_ticks(h.ea_node_id, h.symbol)
                    else:
                        assert client is not None, "client must exist when not is_ea"
                        raw = await fetch_gateway_ticks(
                            client, h.symbol, start_s, now_s,
                        )
                except GatewayError as exc:
                    logger.warning(
                        "paper %s tick poll failed: %s", run_id, exc.detail
                    )
                    continue  # 休市/网关离线容错：降频重试
                fresh = [r for r in raw if int(r[0]) > h.last_tick_msc]
                if not fresh:
                    continue
                for chunk in _chunks(fresh, settings.TICK_MAX_ROWS_PER_SEND):
                    ok = await h.sup.send({"cmd": "ticks", "rows": chunk})
                    if not ok:
                        return  # child gone — stream task finalises
                h.last_tick_msc = fresh[-1][0]
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("paper tick poll %s crashed", run_id)
        finally:
            if client is not None:
                await client.aclose()

    # ── control ────────────────────────────────────────────────

    async def pause(self, run_id: str) -> str:
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                update(PaperRun)
                .where(PaperRun.id == _uuid(run_id), PaperRun.status == "running")
                .values(status="paused")
            )
            await db.commit()
            if not res.rowcount:  # type: ignore[attr-defined]  # SQLAlchemy Result type annotation gap — real type has .rowcount/.scalars()
                row = await db.scalar(select(PaperRun.status).where(
                    PaperRun.id == _uuid(run_id)))
                return row or "not_found"
        h = self._handles.get(run_id)
        if h is not None:
            h.paused.set()
        return "paused"

    async def resume(self, run_id: str) -> str:
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                update(PaperRun)
                .where(PaperRun.id == _uuid(run_id), PaperRun.status == "paused")
                .values(status="running")
            )
            await db.commit()
            if not res.rowcount:  # type: ignore[attr-defined]  # SQLAlchemy Result type annotation gap — real type has .rowcount/.scalars()
                row = await db.scalar(select(PaperRun.status).where(
                    PaperRun.id == _uuid(run_id)))
                return row or "not_found"
        h = self._handles.get(run_id)
        if h is not None:
            h.paused.clear()
        else:
            # process gone (e.g. backend restarted while paused) — re-spawn
            await self._reclaim(run_id)
        return "running"

    async def reset_anchor(self, run_id: str) -> str:
        """Ask the sandbox child to re-anchor its grid to the latest quote.

        Returns "ok", or "not_running" when the child is gone / still replaying.
        """
        h = self._handles.get(run_id)
        if h is None or h.stopping or not h.child_live:
            return "not_running"
        ok = await h.sup.send({"cmd": "reset_anchor"})
        return "ok" if ok else "not_running"

    async def stop(self, run_id: str, close_positions: bool) -> str:
        async with AsyncSessionLocal() as db:
            run = await db.get(PaperRun, _uuid(run_id))
            if run is None:
                return "not_found"
            if run.status in ("stopped", "failed"):
                return run.status
            if run.status == "pending":
                run.status = "failed"
                run.error_code = "CANCELLED"
                run.error_message = "启动前被停止"
                run.stopped_at = datetime.now(UTC)
                db.add(run)
                await db.commit()
                # P0-4 结构化故障输出：启动前被手动取消
                await gate_fault(
                    fault_type=FaultType.STARTUP_FAILED,
                    action_taken="启动前被用户取消",
                    run_id=run.id,
                    detail="启动前被停止",
                ).emit(db=db)
                return "failed"
            await db.execute(
                update(PaperRun)
                .where(PaperRun.id == _uuid(run_id),
                       PaperRun.status.in_(["running", "paused"]))
                .values(status="stopping")
            )
            await db.commit()
        h = self._handles.get(run_id)
        if h is None:
            # no live process (orphaned) — finalise directly
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(PaperRun)
                    .where(PaperRun.id == _uuid(run_id),
                           PaperRun.status.in_(["running", "paused", "stopping"]))
                    .values(status="stopped", stopped_at=datetime.now(UTC))
                )
                await db.commit()
            return "stopped"
        h.stopping = True
        h.paused.clear()  # unblock the send path
        if h.tick_task is not None:
            h.tick_task.cancel()
            h.tick_task = None
        ok = await h.sup.send({"cmd": "stop", "close": close_positions})
        if not ok:
            h.sup.request_cancel()
        return "stopping"

    async def reset(self, run_id: str) -> str:
        """Wipe run history (trades/logs/storage) and start fresh."""
        uid = _uuid(run_id)
        h = self._handles.get(run_id)
        if h is not None:
            h.stopping = True
            h.paused.clear()
            ok = await h.sup.send({"cmd": "stop", "close": False})
            if not ok:
                h.sup.request_cancel()
            # wait for the stream task to finalise (bounded)
            for _ in range(50):
                if run_id not in self._handles:
                    break
                await asyncio.sleep(0.1)
        async with AsyncSessionLocal() as db:
            run = await db.get(PaperRun, uid)
            if run is None:
                return "not_found"
            await db.execute(delete(PaperTrade).where(PaperTrade.run_id == uid))
            await db.execute(delete(PaperLog).where(PaperLog.run_id == uid))
            run.status = "pending"
            run.error_code = None
            run.error_message = None
            run.balance = run.initial_deposit
            run.equity = run.initial_deposit
            run.margin_used = Decimal("0")
            run.margin_free = run.initial_deposit
            run.unrealized_pnl = Decimal("0")
            run.positions = []
            run.last_bar_time = None
            run.equity_history = []
            run.started_at = None
            run.stopped_at = None
            db.add(run)
            await db.commit()
        shutil.rmtree(Path(settings.PAPER_RUNTIME_DIR) / run_id, ignore_errors=True)
        await self.start(run_id)
        return "pending"

    async def _reclaim(self, run_id: str) -> None:
        """Re-spawn a run whose process died (restart-orphan resume path)."""
        uid = _uuid(run_id)
        async with AsyncSessionLocal() as db:
            run = await db.get(PaperRun, uid)
            if run is None:
                return
            run.status = "pending"
            db.add(run)
            await db.commit()
        await self.start(run_id)

    async def _fail(self, run_id: str, code: str, message: str) -> None:
        async with AsyncSessionLocal() as db:
            run = await db.get(PaperRun, _uuid(run_id))
            if run is not None and run.status in ("pending", "running", "paused",
                                                  "stopping"):
                run.status = "failed"
                run.error_code = code[:32]
                run.error_message = message[:8000]
                run.stopped_at = datetime.now(UTC)
                db.add(run)
                await db.commit()
                # P0-4 结构化故障输出：通用失败路径
                ft = FaultType.STRATEGY_CRASHED
                if code == "CANCELLED":
                    ft = FaultType.STARTUP_FAILED
                elif code in ("ILLEGAL_IMPORT", "SANDBOX_VIOLATION"):
                    ft = FaultType.STRATEGY_ILLEGAL_IMPORT
                elif code == ErrorCode.RESOURCE_TIMEOUT.value:
                    ft = FaultType.STRATEGY_TIMEOUT
                await gate_fault(
                    fault_type=ft,
                    action_taken=f"策略失败（code={code}）",
                    run_id=run.id,
                    detail=message,
                ).emit(db=db)


def _chunks(seq: list, size: int) -> list[list]:
    """Split ``seq`` into consecutive chunks of at most ``size`` items."""
    return [seq[i:i + size] for i in range(0, len(seq), size)]


def _ea_bars_to_rows(bars: list) -> list[dict]:
    """EA rates 应答行 → 网关 rates 行结构（_select_history/_rows_to_arrays 同构）。

    行格式 ``[time, open, high, low, close, tick_volume?, spread?]``；time
    与 python 网关 rates 路径同域（MT5 服务器时区标签秒），畸形行跳过。
    """
    rows: list[dict] = []
    for b in bars:
        try:
            rows.append({
                "time": int(b[0]),
                "open": float(b[1]),
                "high": float(b[2]),
                "low": float(b[3]),
                "close": float(b[4]),
                "tick_volume": int(b[5]) if len(b) > 5 else 0,
                "spread": int(b[6]) if len(b) > 6 else 0,
            })
        except (IndexError, TypeError, ValueError):
            continue
    rows.sort(key=lambda r: r["time"])
    return rows


def _ea_account_event(raw: dict, last_ts: int) -> dict:
    """EA sync account 快照 → runner account 事件同构 dict（防漏赋值）。

    键集合与 paper_engine.account_event 完全一致；EA 侧 opened_at 传
    UTC epoch 秒（EA 内已做 skew 归一），此处转 iso；last_bar_time 由
    后端 poll 游标注入（EA 不感知 run 的 bar 轴）。
    """
    out: dict = {
        "balance": float(raw.get("balance", 0.0)),
        "equity": float(raw.get("equity", 0.0)),
        "margin_used": float(raw.get("margin_used", 0.0)),
        "margin_free": float(raw.get("margin_free", 0.0)),
        "unrealized_pnl": float(raw.get("unrealized_pnl", 0.0)),
        "margin_level": raw.get("margin_level"),
        "last_bar_time": (
            datetime.fromtimestamp(last_ts, tz=UTC).isoformat() if last_ts else None
        ),
        "positions": [],
    }
    for p in raw.get("positions") or []:
        try:
            opened = p.get("opened_at")
            out["positions"].append({
                "id": int(p.get("id", 0)),
                "symbol": str(p.get("symbol", "")),
                "direction": str(p.get("direction", "buy")),
                "volume": float(p.get("volume", 0.0)),
                "open_price": float(p.get("open_price", 0.0)),
                "current_price": float(p.get("current_price", 0.0)),
                "unrealized_pnl": float(p.get("unrealized_pnl", 0.0)),
                "realized_pnl": float(p.get("realized_pnl", 0.0)),
                "swap": float(p.get("swap", 0.0)),
                "commission": float(p.get("commission", 0.0)),
                "opened_at": (
                    datetime.fromtimestamp(int(opened), tz=UTC).isoformat()
                    if isinstance(opened, (int, float)) and opened > 0 else None
                ),
            })
        except (TypeError, ValueError):
            continue
    return out


def _select_history(rows: list[dict], tf: int, now_epoch: int, need: int) -> list[dict]:
    """Keep only CLOSED bars (``time + tf <= now``) and trim to the last ``need``.

    The gateway's "latest N" endpoint can return a still-forming live bar;
    feeding it at startup would either be skipped by the poll cursor or fill
    a signal before its period has elapsed.  Weekend/holiday gaps are handled
    upstream by the endpoint's fallback, so we never drop bars for being old.
    """
    closed = [r for r in rows if int(r["time"]) + tf <= now_epoch]
    return closed[-need:] if need > 0 else []


def _rows_to_arrays(rows: list[dict]) -> BarArrays:
    times = sorted({int(r["time"]) for r in rows})
    by_time = {int(r["time"]): r for r in rows}
    return BarArrays(
        time=np.asarray(times, dtype="int64"),
        open=np.asarray([by_time[t]["open"] for t in times], dtype="float64"),
        high=np.asarray([by_time[t]["high"] for t in times], dtype="float64"),
        low=np.asarray([by_time[t]["low"] for t in times], dtype="float64"),
        close=np.asarray([by_time[t]["close"] for t in times], dtype="float64"),
        tick_volume=np.asarray(
            [by_time[t].get("tick_volume", 0) for t in times], dtype="int64"
        ),
        spread=np.asarray([by_time[t].get("spread", 0) for t in times], dtype="int64"),
    )


# ── singleton ──────────────────────────────────────────────────
_manager_singleton: Optional[PaperManager] = None


def get_paper_manager() -> PaperManager:
    global _manager_singleton
    if _manager_singleton is None:
        _manager_singleton = PaperManager()
    return _manager_singleton
