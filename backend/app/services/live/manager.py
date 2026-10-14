"""LiveManager — owns every live (real-money) run (M6).

Clones :class:`app.services.paper.manager.PaperManager` but swaps the virtual
fill for a **real execution bridge**: the child never trades locally; every
``ctx.buy/sell/close`` surfaces as an ``order_request`` event, the parent runs
the pre-trade risk gate, forwards the order to the MT5 gateway, and writes the
``order_result`` back over stdin.  The child's account/positions are a read-only
mirror of the real broker (pushed via ``{"cmd":"account"}`` and inside each
order_result).

    start → symbol-info + history fetch → payload → spawn live_runner →
    stream task persists log/signal/account/fail events + services order_request →
    poll task delivers closed bars + refreshes the broker mirror.

Control semantics mirror paper (pause = stop bar delivery, stop = optional real
close via the gateway, reset = wipe + restart).  ``startup_sweep`` marks
orphaned running/paused/stopping rows failed after a backend restart.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid as uuid_mod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sdk.errors import ErrorCode
from sqlalchemy import delete, select, update

from app.config import settings
from app.engine.session import AsyncSessionLocal
from app.models import LiveLog, LiveRun, LiveTrade, MT5Account, RiskEvent
from app.sandbox.matching import TF_SECONDS
from app.sandbox.payload import BacktestPayload, BarArrays, save_payload
from app.services.alert import emit as alert_emit
from app.services.audit import AuditActor
from app.services.audit import record as audit_record
from app.services.backtest.supervisor import (
    TERM_COMPLETED,
    TERM_STALLED,
    RunnerSupervisor,
)
from app.services.fault_state import (  # P0-4 统一故障状态机
    FaultType,
    gate_fault,
)
from app.services.gateway_client import (
    GatewayClient,
    GatewayError,
    GatewayLoginError,
    GatewayTimeout,
    GatewayTradeNotAllowed,
)
from app.services.gateway_route import (
    account_credentials,
    resolve_gateway,
    resolve_node,
)
from app.services.risk.engine import RiskVerdict, evaluate, load_rules
from app.services.risk.positions import register_opened_position, sync_position_registry
from app.services.strategy_validator import has_callback
from app.services.tick_feed import fetch_ea_buffer_ticks, fetch_gateway_ticks

logger = logging.getLogger("mt5quant.live.manager")

_FLUSH_ROWS = 200
_ACCOUNT_WRITE_INTERVAL = 2.0  # seconds between run-row mirror writes

# -- K线-报价一致性闸门（防"幻影K线"） --------------------------------------
# 终端历史库重同步（账户切换/断线恢复）可能回填错价 bar：时间戳是当前时刻、
# 价格却是另一段行情（2026-10-14 实测：引擎按昨收 4161 的假 bar 连开 10 笔
# 空单，而真实市价 4152~4158）。规则：本批最新一根 bar 的 [low−tol, high+tol]
# 必须包含最新 tick bid，tol = max(3×点差, 0.1%×价)；越界则拒收本批并节流
# 重探历史，连续拒收达上限自动暂停任务。
LIVE_SANITY_MAX_AGE_TFS = 3  # 仅校验"新鲜"批次（最新 bar 距今 ≤ 3×tf）
LIVE_SANITY_TOL_SPREAD_MULT = 3.0  # 容差下限：3 倍点差
LIVE_SANITY_TOL_REL = 0.001  # 容差下限：0.1% 价格
LIVE_SANITY_PAUSE_AFTER = 6  # 连续拒收 N 次（约 1 分钟）自动暂停

# 引擎轮询取数源：from_pos（最新-N 根）。copy_rates_range 走终端历史库区间
# 查询，重同步期间会返回"时间戳新鲜、价格错乱"的幻影 bar；from_pos 直接读
# 最近 K 根，2026-10-14 全程未出错。每次多取些根以覆盖断线补历史。
LIVE_POLL_FETCH_BARS = 300


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


def _chunks(seq: list, size: int) -> list[list]:
    """Split ``seq`` into consecutive chunks of at most ``size`` items."""
    return [seq[i:i + size] for i in range(0, len(seq), size)]


def _select_history(rows: list[dict], tf: int, now_epoch: int, need: int) -> list[dict]:
    """Keep only CLOSED bars (``time + tf <= now``) and trim to the last ``need``."""
    closed = [r for r in rows if int(r["time"]) + tf <= now_epoch]
    return closed[-need:] if need > 0 else []


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
    magic: int
    account_id: str
    gateway_url: str = ""  # M12: node resolved from the bound account at start
    node_type: str = "python"  # python | ea_bridge (P4 EB-1 tick feed branch)
    ea_node_id: str = ""  # registry key when node_type == "ea_bridge"
    paused: asyncio.Event = field(default_factory=asyncio.Event)
    ready: bool = False  # startup replay drained (first account event)
    stopping: bool = False
    busy: bool = False  # an order RPC is in flight — hold bar delivery
    last_ts: int = 0
    last_probe_at: int = 0  # 看门狗: 上次强制历史重同步的时刻（节流 60s）
    sanity_rejects: int = 0  # 一致性闸门: 连续拒收计数（成功过批即清零）
    last_sanity_probe_at: int = 0  # 一致性闸门: 上次因拒收触发重探测（节流 60s）
    # on_tick feed (SDK v1.1.0): in-memory cursor + child-live gate
    has_on_tick: bool = False
    tick_task: Optional[asyncio.Task] = None
    child_live: bool = False  # runner emitted "started" (replay done)
    last_tick_msc: int = 0  # ms cursor; advances only while sending
    account: dict = field(default_factory=dict)  # real broker snapshot
    positions: list = field(default_factory=list)
    logs: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    pending_equity: list = field(default_factory=list)
    last_account_write: float = 0.0
    last_log_flush: float = 0.0
    fail: Optional[dict] = None
    # risk gate state (Phase C)
    rules: list = field(default_factory=list)
    rules_loaded_at: float = 0.0
    order_times: list = field(default_factory=list)  # epoch seconds, last 60s
    day_start_equity: float = 0.0
    day_start_date: str = ""
    peak_equity: float = 0.0
    halt_until: str = ""


class LiveManager:
    def __init__(self) -> None:
        self._handles: dict[str, _RunHandle] = {}
        self._start_tasks: set[asyncio.Task] = set()

    def spawn_start(self, run_id: str) -> None:
        """Fire-and-forget start with a strong task reference (GC guard)."""
        task = asyncio.get_running_loop().create_task(self.start(run_id))
        self._start_tasks.add(task)
        task.add_done_callback(self._start_tasks.discard)

    # ── lifecycle ──────────────────────────────────────────────

    async def startup_sweep(self) -> None:
        """Mark rows orphaned by a backend restart as failed."""
        now = datetime.now(UTC)
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(LiveRun)
                .where(LiveRun.status.in_(["running", "paused", "stopping"]))
                .values(
                    status="failed",
                    error_code="UNKNOWN_ERROR",
                    error_message="服务重启导致实盘中断，请核对持仓后重新启动",
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
        logger.info("live manager stopped")

    def active_count(self) -> int:
        return len(self._handles)

    # ── start / spawn ──────────────────────────────────────────

    async def start(self, run_id: str) -> None:
        """Fetch history + spawn the live_runner for a pending run row."""
        if run_id in self._handles:
            return
        uid = _uuid(run_id)
        async with AsyncSessionLocal() as db:
            run = await db.get(LiveRun, uid)
            if run is None or run.status != "pending":
                return
            res = await db.execute(
                update(LiveRun)
                .where(LiveRun.id == uid, LiveRun.status == "pending")
                .values(status="running", started_at=datetime.now(UTC))
            )
            await db.commit()
            if not res.rowcount:  # type: ignore[attr-defined]  # SQLAlchemy Result type annotation gap — real type has .rowcount/.scalars()
                return  # cancelled between create and claim
            run = await db.get(LiveRun, uid)
            assert run is not None
            if not run.magic:
                run.magic = settings.LIVE_MAGIC_BASE + (uid.int & 0xFFFFFF)
                db.add(run)
                await db.commit()
            magic = run.magic
            account_id = str(run.account_id)
            payload_dir = Path(settings.LIVE_RUNTIME_DIR) / run_id
            # M12: address the node the bound account lives on (MT5 = one
            # terminal = one account) and log in with the stored credentials.
            gateway_url = await resolve_gateway(db, run.account_id)
            node = await resolve_node(db, run.account_id)
            node_type = node.type if node is not None else "python"
            ea_node_id = ""
            if node_type == "ea_bridge":
                assert node is not None, "ea_bridge node must exist when type matches"
                ea_node_id = str(node.id)
            creds = await account_credentials(db, run.account_id)
            node_id = (await db.execute(
                select(MT5Account.gateway_node_id).where(MT5Account.id == run.account_id)
            )).scalar_one_or_none()
            # single-terminal conflict: another active run on the SAME node but
            # a DIFFERENT account would fight for the one MT5 session.
            if node_id is not None:
                clash = (await db.execute(
                    select(LiveRun.id)
                    .join(MT5Account, LiveRun.account_id == MT5Account.id)
                    .where(
                        LiveRun.status.in_(["running", "paused", "stopping"]),
                        MT5Account.gateway_node_id == node_id,
                        LiveRun.account_id != run.account_id,
                    )
                    .limit(1)
                )).scalars().first()
                if clash is not None:
                    await self._fail(
                        run_id, "GATEWAY_CONFLICT",
                        "该网关节点已被其他账户的活动实盘占用——MT5 单终端同一时刻只能"
                        "登录一个账户，请为不同账户配置不同的网关节点后再部署",
                    )
                    return

        if node_type == "ea_bridge":
            # P4 EB-1: tick 上行链路已就绪，启动取数/交易下行 EB-2/3 开放。
            await self._fail(
                run_id, "UNKNOWN_ERROR",
                "EA 桥节点暂不支持部署实盘（完整链路将在下一版本开放）",
            )
            return

        if creds is None:
            await self._fail(
                run_id, "UNKNOWN_ERROR",
                "绑定账户缺少可用凭据（密码密文缺失或解密失败），请重新绑定账户后再部署",
            )
            return

        client = GatewayClient.for_registered_node(gateway_url)
        try:
            login, password, server = creds
            try:
                await client.login(login, password, server)
            except (GatewayLoginError, GatewayError) as exc:
                await self._fail(
                    run_id, "UNKNOWN_ERROR",
                    f"网关登录绑定账户失败（{login}@{server}）：{exc.detail}",
                )
                return
            try:
                symbols = await client.symbols()
            except GatewayError as exc:
                await self._fail(run_id, "UNKNOWN_ERROR", f"网关不可达：{exc.detail}")
                return
            info = next(
                (s for s in symbols if s.get("name", "").upper() == run.symbol.upper()),
                None,
            )
            if info is None:
                await self._fail(run_id, "INVALID_SYMBOL", f"网关不存在品种：{run.symbol}")
                return
            tf = TF_SECONDS[run.timeframe]
            need = run.warmup_bars + settings.LIVE_HISTORY_BARS + 1
            try:
                rows = await client.rates(run.symbol, run.timeframe, min(need, 5000))
            except GatewayError as exc:
                await self._fail(run_id, "UNKNOWN_ERROR", f"取数失败：{exc.detail}")
                return
        finally:
            await client.aclose()

        rows = _select_history(rows, tf, int(time.time()), need)
        if not rows:
            await self._fail(
                run_id, "UNKNOWN_ERROR",
                f"网关无 {run.symbol} {run.timeframe} 历史数据，无法启动实盘",
            )
            return
        arrays = self._rows_to_arrays(rows)
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
            run = await db.get(LiveRun, uid)
            assert run is not None
            snap = dict(run.config_snapshot or {})
            snap["symbol_info"] = payload.symbol_info
            snap["spread_snapshot"] = payload.spread_snapshot
            snap["history_bars"] = len(arrays)
            snap["magic"] = magic
            run.config_snapshot = snap
            # PRD 观察期：单笔手数上限 = 品种最小手数（可调低不可调高）
            if run.observation_ends_at is not None and run.lot_cap is None:
                run.lot_cap = _dec(float(info.get("volume_min", 0.01) or 0.01), 4)
            db.add(run)
            await db.commit()

        h = _RunHandle(
            run_id=run_id,
            sup=RunnerSupervisor(
                run_id, payload_dir, module="app.sandbox.live_runner",
                stall_timeout=float(settings.SANDBOX_STALL_TIMEOUT_SECONDS),
            ),
            stream_task=asyncio.create_task(
                self._stream(run_id), name=f"live-stream-{run_id[:8]}"
            ),
            poll_task=asyncio.create_task(
                self._poll(run_id), name=f"live-poll-{run_id[:8]}"
            ),
            dir=payload_dir,
            symbol=run.symbol,
            timeframe=run.timeframe,
            tf_seconds=tf,
            magic=magic,
            account_id=account_id,
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
                self._tick_poll(run_id), name=f"live-tick-{run_id[:8]}"
            )
        logger.info(
            "live run %s spawned (magic=%d, %d history bars%s)",
            run_id, magic, len(rows), ", on_tick" if h.has_on_tick else "",
        )

    def _rows_to_arrays(self, rows: list[dict]) -> BarArrays:
        import numpy as np

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

    def _build_payload(
        self, run: LiveRun, info: dict, spread_snapshot: float,
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
            execution_mode="standard",  # live fills are real broker prices
            symbol=run.symbol,
            timeframe=run.timeframe,
            tf_seconds=TF_SECONDS[run.timeframe],
            initial_deposit=float(run.allocated_capital),
            leverage=run.leverage,
            spread_mode="current",
            spread_points=None,
            spread_snapshot=spread_snapshot,
            swap_long=float(info.get("swap_long", 0) or 0),
            swap_short=float(info.get("swap_short", 0) or 0),
            commission_per_lot=0.0,
            slippage_points=None,
            warmup_bars=run.warmup_bars,
            parameters=dict(run.parameters or {}),
            seed=int(snap.get("seed") or 0),
            symbol_info=symbol_info,
            config_snapshot=dict(snap),
            callback_timeout_seconds=settings.LIVE_CALLBACK_TIMEOUT,
            on_init_timeout_seconds=float(settings.SANDBOX_ON_INIT_TIMEOUT),
            stop_out_level=0.0,  # unused — no virtual AccountSim in live
            log_limit=settings.LIVE_LOG_LIMIT,
            has_on_tick=has_on_tick,
        )

    # ── stream task (event persistence + order RPC) ────────────

    async def _stream(self, run_id: str) -> None:
        h = self._handles.get(run_id)
        assert h is not None
        try:
            result = await h.sup.run(self._on_event(run_id), timeout=None)
        except Exception:  # noqa: BLE001
            logger.exception("live stream %s crashed", run_id)
            await self._fail(run_id, "UNKNOWN_ERROR", "实盘编排异常，请核对持仓后重新启动")
            self._handles.pop(run_id, None)
            return

        await self._flush_rows(run_id)
        await self._write_account(run_id, force=True)
        uid = _uuid(run_id)
        now = datetime.now(UTC)
        async with AsyncSessionLocal() as db:
            run = await db.get(LiveRun, uid)
            if run is None:
                self._handles.pop(run_id, None)
                return
            if result.outcome == TERM_COMPLETED:
                run.status = "stopped"
                run.stopped_at = now
            elif result.outcome == TERM_STALLED:
                # 卡死杀进程时不会有 fail 事件——父侧自己得把原因说清楚，
                # 否则用户只看得到「沙箱进程异常退出」。实盘下这通常意味着
                # 有持仓而策略已失控，所以文案要显式提示人工核对。
                run.status = "failed"
                run.stopped_at = now
                run.error_code = ErrorCode.RESOURCE_TIMEOUT.value
                run.error_message = (
                    f"策略进程连续 {settings.SANDBOX_STALL_TIMEOUT_SECONDS} 秒无任何响应"
                    f"（含心跳），已判定卡死并终止；请人工核对持仓与挂单，"
                    f"必要时在对账页确认收敛。"
                    f"\n{result.stderr_tail[-1500:]}"
                )[:8000]
                # P0-4 结构化故障输出：策略卡死
                await gate_fault(
                    fault_type=FaultType.STRATEGY_TIMEOUT,
                    action_taken="判定卡死并终止沙箱进程",
                    run_id=run.id,
                    account_id=run.account_id,
                    detail=run.error_message,
                ).emit(db=db)
            else:
                fail = h.fail or {}
                run.status = "failed"
                run.stopped_at = now
                run.error_code = str(fail.get("code") or "UNKNOWN_ERROR")[:32]
                msg = str(fail.get("message") or "沙箱进程异常退出")
                tb = str(fail.get("traceback") or "")
                if run.error_code == ErrorCode.RESOURCE_TIMEOUT.value:
                    # 回调被熔断打断 ≠ 券商那边什么都没发生：此前发出的单子不会
                    # 因为策略失败而撤销，而账户镜像停在打断前那一刻。不写这句，
                    # 用户会把「运行失败」读成「没有仓位」——那正是失控的起点。
                    # （成交本身不需要标记：live_trades 来自券商成交回报，是既成
                    # 事实；可疑的是账户镜像的时效性。）
                    msg += (
                        "\n注意：策略是在回调中途被终止的，此前发出的订单不会随之撤销。"
                        "账户镜像可能落后于券商实际状态，请在对账页核对持仓与挂单。"
                    )
                run.error_message = (msg + (f"\n{tb[-1500:]}" if tb else ""))[:8000]
                # P0-4 结构化故障输出：策略崩溃 / 非法 import / 其他运行时异常
                ft = FaultType.STRATEGY_CRASHED
                if run.error_code in ("ILLEGAL_IMPORT", "SANDBOX_VIOLATION"):
                    ft = FaultType.STRATEGY_ILLEGAL_IMPORT
                await gate_fault(
                    fault_type=ft,
                    action_taken=f"策略进程异常退出（code={run.error_code}）",
                    run_id=run.id,
                    account_id=run.account_id,
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
                    # startup replay drained — allow bar delivery from now on
                    h.ready = True
                    h.paused.clear()
            elif kind == "started":
                h.child_live = True  # replay done — ticks may flow
            elif kind == "fail":
                h.fail = dict(event)
            elif kind == "order_request":
                await self._handle_order_request(run_id, event)
            if len(h.logs) + len(h.trades) >= _FLUSH_ROWS:
                await self._flush_rows(run_id)
                h.last_log_flush = time.monotonic()
            elif h.logs and time.monotonic() - h.last_log_flush >= 5.0:
                # idle-market safety net: don't hold low-volume lines forever
                await self._flush_rows(run_id)
                h.last_log_flush = time.monotonic()
        return on_event

    # ── order RPC: risk gate → gateway → order_result ──────────

    async def _recent_deals_for_risk(self, h: _RunHandle) -> list[dict] | None:
        """拉取近 14 天成交记录（仅连续亏损冷却规则需要）。

        网关异常时返回 None → 冷却规则跳过本次评估，不阻断下单路径。
        """
        try:
            client = GatewayClient.for_registered_node(h.gateway_url or None)
            try:
                end = int(time.time())
                return await client.deals(end - 14 * 86400, end)
            finally:
                await client.aclose()
        except Exception:  # noqa: BLE001 — 历史拉取失败不能拖垮订单链路
            logger.exception("live %s recent-deals fetch for risk gate failed", h.run_id)
            return None

    async def _handle_order_request(self, run_id: str, req: dict) -> None:
        h = self._handles.get(run_id)
        if h is None:
            return
        h.busy = True
        req_id = req.get("req_id", "")
        op = str(req.get("op", "buy"))
        symbol = str(req.get("symbol", h.symbol))
        volume = req.get("volume")
        position_id = req.get("position_id")
        comment = str(req.get("comment", ""))
        result: dict = {
            "type": "order_result", "req_id": req_id, "success": False,
            "order_id": None, "filled_price": None, "filled_volume": None,
            "commission": 0.0, "error_code": "UNKNOWN_ERROR",
            "error_message": "内部错误", "account": self._mirror_payload(h),
        }
        try:
            async with AsyncSessionLocal() as db:
                run = await db.get(LiveRun, _uuid(run_id))
                run_dict = run.to_dict() if run is not None else {}
                acct_frozen = False
                algo_enabled = True
                if run is not None:
                    acct = await db.get(MT5Account, run.account_id)
                    acct_frozen = bool(acct is not None and acct.trade_frozen)
                    # 算法交易账户开关：False → 策略自动下单全部拒绝（开仓/平仓均拦截）
                    algo_enabled = bool(acct is None or acct.algo_trading_enabled)
            run_dict["account_frozen"] = acct_frozen
            run_dict["algo_trading_enabled"] = algo_enabled
            if not algo_enabled:
                result.update({
                    "error_code": "ALGO_TRADING_DISABLED",
                    "error_message": (
                        "算法交易已禁止 — 该账户的策略自动下单被拒绝"
                        "（可在 K 线图页顶部开关恢复）"
                    ),
                })
                await h.sup.send(result)
                return
            run_dict["halt_until"] = h.halt_until
            run_dict["day_start_equity"] = h.day_start_equity
            run_dict["peak_equity"] = h.peak_equity
            await self._ensure_rules(h)
            now = time.time()
            h.order_times = [t for t in h.order_times if now - t < 60.0]
            recent_deals = None
            closed_trades = None
            registry_open_times = None
            if op in ("buy", "sell") and any(
                r.get("type") in ("consecutive_loss_cooldown", "order_rate_window")
                and str(r.get("apply_to") or "both") in ("both", "ai")
                for r in h.rules
            ):
                # 优先持仓登记表（按票号直查，不依赖终端历史同步）
                try:
                    rc_client = GatewayClient.for_registered_node(h.gateway_url or None)
                    try:
                        closed_trades, registry_open_times = await sync_position_registry(
                            rc_client, str(h.account_id), h.positions
                        )
                    finally:
                        await rc_client.aclose()
                except Exception:  # noqa: BLE001 — 登记失败不阻断下单
                    logger.exception("live %s position-registry sync failed", h.run_id)
                    closed_trades = None
                if closed_trades is None:
                    recent_deals = await self._recent_deals_for_risk(h)
            verdict = evaluate(
                op=op, symbol=symbol, volume=volume, position_id=position_id,
                run=run_dict, account=h.account, positions=h.positions,
                rules=h.rules, symbol_info=self._risk_symbol_info(run_dict),
                orders_this_minute=len(h.order_times),
                recent_deals=recent_deals,
                closed_trades=closed_trades,
                registry_open_times=registry_open_times,
                order_source="ai",
            )
            if not verdict.allow:
                result.update({
                    "error_code": verdict.error_code or "RISK_BLOCKED",
                    "error_message": verdict.error_message or "风控拦截",
                })
                await self._apply_risk_action(run_id, h, verdict, req)
                await h.sup.send(result)
                return
            h.order_times.append(now)
            # forward to the gateway
            result = await self._execute_at_gateway(h, req_id, op, symbol, volume,
                                                    position_id, comment, req)
            if result.get("success") and result.get("order"):
                # 立即登记票号（持仓登记表），否则快速开平的单子对风控不可见
                try:
                    await register_opened_position(
                        str(h.account_id), int(result["order"]), symbol,
                        open_time=int(time.time()),
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("live %s register ticket failed", h.run_id)
        except Exception as exc:  # noqa: BLE001 — never let one order kill the stream
            logger.exception("live order RPC %s failed", run_id)
            result.update({
                "error_code": "UNKNOWN_ERROR",
                "error_message": f"下单中转异常：{exc}",
            })
            # P0-4 外层 RPC 中转异常：覆盖 _execute_at_gateway 内部未捕获的异常
            # （如风控代码 bug、ticket 注册异常等），与内层 FaultRecord 不重复
            try:
                async with AsyncSessionLocal() as db:
                    await gate_fault(
                        fault_type=FaultType.ORDER_FAILED,
                        action_taken=f"下单中转异常：{exc}",
                        run_id=run_id,
                        account_id=h.account_id,
                        detail=f"op={op} symbol={symbol} {exc}",
                    ).emit(db=db, level="critical")
            except Exception:  # noqa: BLE001 — emit 失败不能阻断订单流
                logger.exception("order fault emit (outer) failed: run_id=%s", run_id)
        finally:
            h.busy = False
        await h.sup.send(result)

    async def _execute_at_gateway(
        self, h: _RunHandle, req_id: str, op: str, symbol: str,
        volume, position_id, comment: str, req: dict | None = None,
    ) -> dict:
        out: dict = {
            "type": "order_result", "req_id": req_id, "success": False,
            "order_id": None, "filled_price": None, "filled_volume": None,
            "commission": 0.0, "error_code": None, "error_message": None,
            "account": self._mirror_payload(h),
        }
        client = GatewayClient.for_registered_node(h.gateway_url or None)
        try:
            if op in ("buy", "sell"):
                gw = await client.place_market(
                    symbol, float(volume), op, magic=h.magic,
                    comment=f"{h.run_id[:8]}:{req_id[:8]}",
                    timeout=settings.LIVE_ORDER_TIMEOUT,
                )
            elif op == "close":
                gw = await client.close_position(
                    int(position_id), float(volume) if volume else None,
                    magic=h.magic, timeout=settings.LIVE_ORDER_TIMEOUT,
                )
            else:
                out["error_code"] = "INVALID_SYMBOL"
                out["error_message"] = f"未知操作：{op}"
                gw = {"success": False}  # 占位，让下面的 if gw.get("success") 跳过
            if gw.get("success"):
                out.update({
                    "success": True,
                    "order_id": str(gw.get("order") or gw.get("deal") or ""),
                    "filled_price": float(gw.get("price", 0.0)),
                    "filled_volume": float(gw.get("volume", 0.0)),
                    "commission": 0.0,
                })
                await self._record_deal(h, op, gw, req)
            else:
                out.update({
                    "error_code": self._retcode_to_error(int(gw.get("retcode", -1))),
                    "error_message": gw.get("retcode_text") or "券商拒单",
                })
        except GatewayTimeout:
            # request reached the gateway but no response — outcome UNKNOWN.
            # Do NOT retry; flag for reconciliation (Phase E converges it).
            out.update({
                "error_code": "UNKNOWN_ERROR",
                "error_message": "下单结果未知（网关超时），已转对账收敛",
            })
            await self._flag_reconcile(h.run_id)
        except GatewayTradeNotAllowed as exc:
            out.update({"error_code": "RISK_BLOCKED", "error_message": exc.detail})
        except GatewayError as exc:
            # connect refused / gateway down → the order was NOT sent.
            out.update({"error_code": "UNKNOWN_ERROR", "error_message": exc.detail})
        finally:
            await self._refresh_mirror(h, client)
            await client.aclose()
            out["account"] = self._mirror_payload(h)

        # P0-4 订单失败统一结构化输出：一个 emit 覆盖所有 5 种失败分支
        # （券商拒单 / 网关超时 / 风控拦截 / 网关连接失败 / 无效操作）
        if not out["success"]:
            await self._emit_order_fault(h, op, symbol, out)
        return out

    @staticmethod
    def _retcode_to_error(retcode: int) -> str:
        if retcode in (10018,):
            return "INSUFFICIENT_MARGIN"
        if retcode in (10020, 10024, 10034):
            return "MARKET_CLOSED"
        if retcode in (10013, 10014, 10015):
            return "INVALID_VOLUME"
        if retcode in (10004, 10019):
            return "REQUOTE"
        return "UNKNOWN_ERROR"

    async def _emit_order_fault(
        self, h: _RunHandle, op: str, symbol: str, out: dict,
    ) -> None:
        """P0-4 订单失败统一 FaultRecord emit。

        按 error_code 区分故障级别：风控拦截 = critical，超时/未知 = warn，
        券商业务拒单（保证金/停盘/手数）= warn（这些是正常失败，不是系统故障）。
        """
        code = str(out.get("error_code") or "UNKNOWN_ERROR")
        level = "warn"
        action = f"订单操作 {op} {symbol} 失败"
        if code == "RISK_BLOCKED":
            level = "critical"
            action = f"风控拦截：{out.get('error_message', '')}"
        elif code == "UNKNOWN_ERROR" and "超时" in str(out.get("error_message", "")):
            action = "下单结果未知（网关超时），已转对账收敛"
        elif code == "MARKET_CLOSED":
            action = "市场关闭，订单被券商拒绝"
        elif code == "INSUFFICIENT_MARGIN":
            action = "保证金不足，订单被券商拒绝"
        elif code == "INVALID_VOLUME":
            action = "手数不合法，订单被券商拒绝"
        elif code == "REQUOTE":
            action = "价格已变，需要 requote 重试"

        async with AsyncSessionLocal() as db:
            try:
                await gate_fault(
                    fault_type=FaultType.ORDER_FAILED,
                    action_taken=action,
                    run_id=h.run_id,
                    account_id=h.account_id,
                    detail=f"op={op} symbol={symbol} error_code={code} "
                           f"error_message={out.get('error_message', '')}",
                ).emit(db=db, level=level)
            except Exception:  # noqa: BLE001 — emit 失败不能阻断订单流
                logger.exception("order fault emit failed: run_id=%s", h.run_id)

    async def _record_deal(
        self, h: _RunHandle, op: str, gw: dict, req: dict | None = None,
    ) -> None:
        """Persist one real deal as a LiveTrade row (best-effort).

        Execution-quality columns (M4): request-time bid/ask from the gateway
        snapshot + the signal-bar reference carried on the order_request, so
        slippage calibration and the deviation report can be computed later.
        """
        try:
            entry = "out" if op == "close" else "in"
            direction = "sell" if op == "close" else op
            sig_price = sig_spread = None
            sig_bar_time = None
            if req:
                sig_price = req.get("signal_price")
                sig_spread = req.get("signal_spread")
                raw_bt = req.get("bar_time")
                if raw_bt:
                    try:
                        sig_bar_time = datetime.fromisoformat(str(raw_bt))
                    except ValueError:
                        sig_bar_time = None
            bid_at_req = gw.get("bid")
            ask_at_req = gw.get("ask")
            async with AsyncSessionLocal() as db:
                db.add(LiveTrade(
                    run_id=_uuid(h.run_id),
                    deal_ticket=int(gw.get("deal") or 0),
                    order_ticket=int(gw.get("order") or 0),
                    position_id=str(gw.get("order") or gw.get("deal") or ""),
                    direction=direction,
                    entry=entry,
                    volume=_dec(gw.get("volume", 0.0), 4),
                    open_time=datetime.now(UTC),
                    price=_dec(gw.get("price", 0.0)),
                    bid_at_request=(
                        _dec(bid_at_req, 8) if bid_at_req is not None else None
                    ),
                    ask_at_request=(
                        _dec(ask_at_req, 8) if ask_at_req is not None else None
                    ),
                    signal_price=(
                        _dec(sig_price, 8) if sig_price is not None else None
                    ),
                    signal_spread_points=(
                        _dec(sig_spread, 2) if sig_spread is not None else None
                    ),
                    signal_bar_time=sig_bar_time,
                    close_time=datetime.now(UTC) if entry == "out" else None,
                    profit=Decimal("0"),
                    retcode=int(gw.get("retcode", 0) or 0),
                    comment=str(gw.get("comment", ""))[:200],
                ))
                await db.commit()
        except Exception:  # noqa: BLE001 — a duplicate deal ticket is fine
            logger.warning("live deal persist skipped run=%s", h.run_id)

    async def _record_risk_log(self, run_id: str, verdict, req: dict) -> None:
        h = self._handles.get(run_id)
        if h is None:
            return
        h.logs.append({
            "seq": 0,  # renumbered on flush by the stream's own seq
            "kind": "risk",
            "level": "warn",
            "message": f"风控拦截 [{verdict.error_code}] {verdict.error_message}",
            "payload": {"op": req.get("op"), "symbol": req.get("symbol"),
                        "volume": req.get("volume"), "rule": verdict.rule_id},
        })

    # ── risk gate helpers (Phase C) ────────────────────────────

    async def _ensure_rules(self, h: _RunHandle) -> None:
        """Reload the scoped rule set at most every 5s (cheap, live-editable)."""
        if time.monotonic() - h.rules_loaded_at < 5.0:
            return
        try:
            h.rules = await load_rules(h.run_id, h.account_id)
        except Exception:  # noqa: BLE001 — DB blip must not block trading
            logger.exception("live %s rule load failed", h.run_id)
        h.rules_loaded_at = time.monotonic()

    @staticmethod
    def _risk_symbol_info(run_dict: dict) -> dict:
        snap = run_dict.get("config_snapshot") or {}
        info = dict(snap.get("symbol_info") or {})
        return info

    async def _apply_risk_action(
        self, run_id: str, h: _RunHandle, verdict: RiskVerdict, req: dict
    ) -> None:
        """Persist the trigger (log + RiskEvent) and execute the action."""
        await self._record_risk_log(run_id, verdict, req)
        try:
            async with AsyncSessionLocal() as db:
                run = await db.get(LiveRun, _uuid(run_id))
                db.add(RiskEvent(
                    run_id=_uuid(run_id),
                    account_id=run.account_id if run is not None else None,
                    rule_id=_uuid(verdict.rule_id) if verdict.rule_id else None,
                    rule_type=verdict.rule_type or "unknown",
                    source="live",
                    threshold=_dec(verdict.threshold),
                    observed=_dec(verdict.observed),
                    action_taken=verdict.action or "reject",
                    detail={"order": {k: req.get(k) for k in
                                      ("op", "symbol", "volume", "position_id")},
                            "message": verdict.error_message},
                ))
                if run is not None:
                    if verdict.action == "halt_open_today":
                        run.halt_until = (datetime.now(UTC) + timedelta(days=1)
                                          ).strftime("%Y-%m-%d")
                        run.no_new_orders = True
                        h.halt_until = run.halt_until
                        # PRD: observation-period trigger resets the ramp timer
                        if run.observation_ends_at is not None:
                            run.observation_ends_at = datetime.now(UTC) + timedelta(
                                days=settings.LIVE_OBSERVATION_DAYS)
                    db.add(run)
                await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("live %s risk event persist failed", run_id)
        if verdict.action == "close_all_and_stop":
            asyncio.create_task(self._close_all_and_stop(run_id))

    async def _flag_reconcile(self, run_id: str) -> None:
        async with AsyncSessionLocal() as db:
            run = await db.get(LiveRun, _uuid(run_id))
            if run is not None:
                run.reconcile_pending = True
                db.add(run)
                await db.commit()

    # ── broker mirror ──────────────────────────────────────────

    def _mirror_payload(self, h: _RunHandle) -> dict:
        return {"account": h.account, "positions": h.positions}

    async def _refresh_mirror(self, h: _RunHandle, client: GatewayClient | None) -> None:
        own = client is None
        if own:
            client = GatewayClient.for_registered_node(h.gateway_url or None)
        assert client is not None, "client must be set after _refresh_mirror init"
        try:
            acc = await client.account()
            positions = await client.positions()
            if acc is not None:
                h.account = acc
            h.positions = positions or []
            await self._push_account(h)
            await self._write_account(h.run_id)
        except GatewayError as exc:
            logger.warning("live %s mirror refresh failed: %s", h.run_id, exc.detail)
        finally:
            if own:
                await client.aclose()

    async def _push_account(self, h: _RunHandle) -> None:
        await h.sup.send({"cmd": "account", "account": self._mirror_payload(h)})

    async def _write_account(self, run_id: str, force: bool = False) -> None:
        h = self._handles.get(run_id)
        if h is None or not h.account:
            return
        if not force and time.monotonic() - h.last_account_write < _ACCOUNT_WRITE_INTERVAL:
            return
        a = h.account
        uid = _uuid(run_id)
        async with AsyncSessionLocal() as db:
            run = await db.get(LiveRun, uid)
            if run is None or run.status in ("stopped", "failed"):
                return
            eq = float(a.get("equity", 0.0))
            bal = float(a.get("balance", 0.0))
            eq_hist = list(run.equity_history or [])
            eq_hist.append({"time": datetime.now(UTC).isoformat(),
                            "equity": eq, "balance": bal})
            if len(eq_hist) > settings.LIVE_EQUITY_POINTS_MAX:
                eq_hist = eq_hist[-settings.LIVE_EQUITY_POINTS_MAX:]
            run.balance = _dec(bal, 4)
            run.equity = _dec(eq, 4)
            run.margin_used = _dec(a.get("margin", 0.0), 4)
            run.margin_free = _dec(a.get("margin_free", 0.0), 4)
            run.unrealized_pnl = _dec(eq - bal, 4)
            # risk state rolling (Phase C evaluator reads these via the handle)
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            if run.day_start_date != today or run.day_start_equity == Decimal("0"):
                run.day_start_date = today
                run.day_start_equity = _dec(eq, 4)
            if eq > float(run.peak_equity):
                run.peak_equity = _dec(eq, 4)
            if run.halt_until and run.halt_until < today:
                run.halt_until = ""
                run.no_new_orders = False
            h.day_start_equity = float(run.day_start_equity)
            h.peak_equity = float(run.peak_equity)
            h.day_start_date = run.day_start_date
            h.halt_until = run.halt_until
            run.positions = list(h.positions)
            run.equity_history = eq_hist
            db.add(run)
            await db.commit()
        h.last_account_write = time.monotonic()

    async def _flush_rows(self, run_id: str) -> None:
        h = self._handles.get(run_id)
        if h is None or not h.logs:
            return
        uid = _uuid(run_id)
        async with AsyncSessionLocal() as db:
            max_seq = await db.scalar(
                select(LiveLog.seq).where(LiveLog.run_id == uid)
                .order_by(LiveLog.seq.desc()).limit(1)
            )
            seq = int(max_seq or 0)
            for ev in h.logs:
                seq += 1
                db.add(LiveLog(
                    run_id=uid,
                    seq=seq,
                    bar_time=_dt(ev.get("bar_time")),
                    kind=str(ev.get("kind", "log")),
                    level=str(ev.get("level", "info")),
                    message=str(ev.get("message", ""))[:2000],
                    payload=ev.get("payload"),
                ))
            h.logs.clear()
            await db.commit()

    # ── poll task (closed-bar delivery + mirror refresh) ───────

    async def _poll(self, run_id: str) -> None:
        h = self._handles.get(run_id)
        assert h is not None
        client = GatewayClient.for_registered_node(h.gateway_url or None)
        try:
            while True:
                await asyncio.sleep(settings.LIVE_POLL_SECONDS)
                cur = self._handles.get(run_id)
                if cur is not h:
                    return  # handle replaced/removed
                if h.paused.is_set() or h.stopping:
                    continue
                # refresh the real broker mirror every poll tick
                await self._refresh_mirror(h, client)
                if h.busy:
                    continue
                now = int(time.time())
                end = now - h.tf_seconds + 1
                if end <= h.last_ts:
                    continue
                try:
                    rows = await client.rates(
                        h.symbol, h.timeframe, LIVE_POLL_FETCH_BARS,
                    )
                except GatewayError as exc:
                    logger.warning("live %s poll failed: %s", run_id, exc.detail)
                    continue
                fresh = [
                    [r["time"], r["open"], r["high"], r["low"], r["close"],
                     r.get("tick_volume", 0), r.get("spread", 0)]
                    for r in rows
                    if int(r["time"]) > h.last_ts and int(r["time"]) + h.tf_seconds <= now
                ]
                if not fresh:
                    await self._maybe_reprobe(h, client, now, run_id)
                    continue
                if not await self._sanity_gate(h, client, fresh, now, run_id):
                    continue  # 幻影K线：拒收本批，不喂引擎、不推进 last_ts
                ok = await h.sup.send({"cmd": "bars", "rows": fresh})
                if not ok:
                    return  # child gone — stream task finalises
                h.last_ts = max(int(r[0]) for r in fresh)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("live poll %s crashed", run_id)
        finally:
            await client.aclose()

    async def _sanity_gate(
        self, h: _RunHandle, client: GatewayClient,
        fresh: list[list], now: int, run_id: str,
    ) -> bool:
        """K线-报价一致性闸门：拦下"幻影K线"，防止引擎在假数据上交易。

        校验本批最新一根 bar：最新 tick bid 必须落在 ``[low−tol, high+tol]``
        内，``tol = max(3×点差, 0.1%×价)``。越界即认定历史库回填了错价
        （时间戳新鲜、价格属于另一段行情），拒收整批。返回 False 表示已拒收：
        调用方不得推进 ``last_ts``（下一轮重新拉取同一窗口，数据恢复后自动
        补上）。拿不到新鲜报价（休市/掉线）或批次过老时跳过校验，交给
        看门狗/_maybe_reprobe 处理，避免误伤。
        """
        newest = max(fresh, key=lambda r: int(r[0]))
        bar_time = int(newest[0])
        if bar_time + h.tf_seconds < now - LIVE_SANITY_MAX_AGE_TFS * h.tf_seconds:
            return True  # 批次过老（补历史/恢复态），无从对价，放行交给其它防线
        try:
            q = await client.quotes([h.symbol])
        except GatewayError:
            return True  # 拿不到报价不拦，保持可用性
        qt = next((x for x in q if x.get("symbol") == h.symbol), None)
        if qt is None:
            return True
        try:
            bid = float(qt.get("bid") or 0)
            ask = float(qt.get("ask") or 0)
            quote_ts = int(qt.get("time") or 0)
        except (TypeError, ValueError):
            return True
        if bid <= 0 or ask <= 0 or now - quote_ts > 180:
            return True  # 报价缺失或已停跳，无从校验
        # fresh 行格式 [time, open, high, low, close, ...]：索引 2=high、3=low
        low, high = float(newest[3]), float(newest[2])
        tol = max(LIVE_SANITY_TOL_SPREAD_MULT * (ask - bid),
                  LIVE_SANITY_TOL_REL * bid)
        if low - tol <= bid <= high + tol:
            h.sanity_rejects = 0
            return True
        h.sanity_rejects += 1
        h.logs.append({
            "kind": "log", "level": "error",
            "message": (
                f"K线-报价一致性校验失败(第{h.sanity_rejects}次)："
                f"bar[{datetime.fromtimestamp(bar_time, tz=UTC).strftime('%H:%M')}] "
                f"{low:.2f}~{high:.2f} "
                f"与最新报价 {bid:.2f} 脱离(容差±{tol:.2f})，"
                f"已拒收本批 {len(fresh)} 根K线，不喂给策略引擎"
            ),
        })
        logger.error(
            "live %s sanity gate rejected %d bars: bar[%d] %.2f~%.2f vs bid %.2f",
            run_id, len(fresh), bar_time, low, high, bid,
        )
        if now - h.last_sanity_probe_at > 60:
            h.last_sanity_probe_at = now
            try:
                await client.probe_history(h.symbol, h.timeframe)
                logger.warning(
                    "live %s 检测到幻影K线，已触发强制历史重同步", run_id)
            except GatewayError as exc:
                logger.warning("live %s sanity probe failed: %s",
                               run_id, exc.detail)
        if h.sanity_rejects >= LIVE_SANITY_PAUSE_AFTER:
            uid = _uuid(run_id)
            h.logs.append({
                "kind": "log", "level": "error",
                "message": (
                    f"连续 {h.sanity_rejects} 次K线数据校验失败（终端历史库疑似"
                    "回填错价），已自动暂停任务；请在行情恢复后手动恢复运行"
                ),
            })
            logger.critical("live %s auto-paused: %d consecutive sanity "
                            "rejections", run_id, h.sanity_rejects)
            await alert_emit(
                "sanity_gate", "实盘任务因K线数据异常已自动暂停",
                f"任务 {run_id} 连续 {h.sanity_rejects} 次收到与实时报价严重"
                "脱节的K线数据（疑似终端历史库回填错价）。为防止在假数据上"
                "交易，任务已自动暂停，请核对行情后手动恢复。",
                level="critical", account_id=_uuid(h.account_id), run_id=uid,
            )
            await self.pause(run_id)
        return False

    async def _maybe_reprobe(
        self, h: _RunHandle, client: GatewayClient, now: int, run_id: str
    ) -> None:
        """K 线断流看门狗。

        报价仍在跳动但已超过一根 K 线周期 + 90s 宽限没有新 bar（终端切换
        账户/重连后历史停止同步的典型症状）时，调 ``probe_history`` 强制
        终端重新同步历史，避免引擎静默断粮。60s 节流；报价也停了（休市/
        终端掉线）则不动。
        """
        if now - h.last_ts < h.tf_seconds + 90:
            return
        if now - h.last_probe_at < 60:
            return
        try:
            q = await client.quotes([h.symbol])
        except GatewayError:
            return
        fresh_quote = next((x for x in q if x.get("symbol") == h.symbol), None)
        if fresh_quote is None:
            return
        try:
            quote_ts = int(fresh_quote.get("time") or 0)
        except (TypeError, ValueError):
            return
        if quote_ts <= 0 or now - quote_ts > 180:
            return  # 报价也停了 → 休市/终端掉线，探了也没用
        h.last_probe_at = now
        try:
            info = await client.probe_history(h.symbol, h.timeframe)
            logger.warning(
                "live %s K线断流看门狗触发: 已 %ss 无新K线且报价仍在跳动, "
                "已强制重同步历史 (last_bar_time=%s)",
                run_id, now - h.last_ts, info.get("last_bar_time"),
            )
        except GatewayError as exc:
            logger.warning("live %s 历史重同步失败: %s", run_id, exc.detail)

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
                if h.busy:
                    continue  # order RPC in flight — hold tick delivery
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
                        "live %s tick poll failed: %s", run_id, exc.detail
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
            logger.exception("live tick poll %s crashed", run_id)
        finally:
            if client is not None:
                await client.aclose()

    # ── control ────────────────────────────────────────────────

    async def pause(self, run_id: str) -> str:
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                update(LiveRun)
                .where(LiveRun.id == _uuid(run_id), LiveRun.status == "running")
                .values(status="paused")
            )
            await db.commit()
            if not res.rowcount:  # type: ignore[attr-defined]  # SQLAlchemy Result type annotation gap — real type has .rowcount/.scalars()
                row = await db.scalar(select(LiveRun.status).where(
                    LiveRun.id == _uuid(run_id)))
                return row or "not_found"
        h = self._handles.get(run_id)
        if h is not None:
            h.paused.set()
        return "paused"

    async def resume(self, run_id: str) -> str:
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                update(LiveRun)
                .where(LiveRun.id == _uuid(run_id), LiveRun.status == "paused")
                .values(status="running")
            )
            await db.commit()
            if not res.rowcount:  # type: ignore[attr-defined]  # SQLAlchemy Result type annotation gap — real type has .rowcount/.scalars()
                row = await db.scalar(select(LiveRun.status).where(
                    LiveRun.id == _uuid(run_id)))
                return row or "not_found"
        h = self._handles.get(run_id)
        if h is not None:
            h.paused.clear()
        else:
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
            run = await db.get(LiveRun, _uuid(run_id))
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
                return "failed"
            await db.execute(
                update(LiveRun)
                .where(LiveRun.id == _uuid(run_id),
                       LiveRun.status.in_(["running", "paused"]))
                .values(status="stopping")
            )
            await db.commit()
        h = self._handles.get(run_id)
        if h is None:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(LiveRun)
                    .where(LiveRun.id == _uuid(run_id),
                           LiveRun.status.in_(["running", "paused", "stopping"]))
                    .values(status="stopped", stopped_at=datetime.now(UTC))
                )
                await db.commit()
            return "stopped"
        if close_positions:
            await self._gateway_close_all(h)
        h.stopping = True
        h.paused.clear()
        if h.tick_task is not None:
            h.tick_task.cancel()
            h.tick_task = None
        ok = await h.sup.send({"cmd": "stop", "close": False})
        if not ok:
            h.sup.request_cancel()
        return "stopping"

    async def reset(self, run_id: str) -> str:
        uid = _uuid(run_id)
        h = self._handles.get(run_id)
        if h is not None:
            h.stopping = True
            h.paused.clear()
            ok = await h.sup.send({"cmd": "stop", "close": False})
            if not ok:
                h.sup.request_cancel()
            for _ in range(50):
                if run_id not in self._handles:
                    break
                await asyncio.sleep(0.1)
        async with AsyncSessionLocal() as db:
            run = await db.get(LiveRun, uid)
            if run is None:
                return "not_found"
            await db.execute(delete(LiveTrade).where(LiveTrade.run_id == uid))
            await db.execute(delete(LiveLog).where(LiveLog.run_id == uid))
            run.status = "pending"
            run.error_code = None
            run.error_message = None
            run.equity_history = []
            run.last_bar_time = None
            run.started_at = None
            run.stopped_at = None
            db.add(run)
            await db.commit()
        shutil.rmtree(Path(settings.LIVE_RUNTIME_DIR) / run_id, ignore_errors=True)
        await self.start(run_id)
        return "pending"

    # ── real close-all helpers (stop-with-close / Kill / risk) ──

    async def _gateway_close_all(self, h: _RunHandle) -> None:
        client = GatewayClient.for_registered_node(h.gateway_url or None)
        try:
            positions = await client.positions()
            for p in positions or []:
                if int(p.get("magic", 0)) == h.magic:
                    try:
                        await client.close_position(
                            int(p["ticket"]), None, magic=h.magic,
                            timeout=settings.LIVE_ORDER_TIMEOUT,
                        )
                    except GatewayError as exc:
                        logger.warning("live %s close-all skip %s: %s",
                                       h.run_id, p.get("ticket"), exc.detail)
        except GatewayError as exc:
            logger.warning("live %s close-all failed: %s", h.run_id, exc.detail)
        finally:
            await client.aclose()

    async def _close_all_and_stop(self, run_id: str) -> None:
        """Risk action close_all_and_stop: real close + stop + auto-demote."""
        h = self._handles.get(run_id)
        if h is not None:
            await self._gateway_close_all(h)
        await self.stop(run_id, close_positions=False)
        await self._demote(run_id, reason="触发「全平仓+停止策略」级风控规则")

    async def _demote(self, run_id: str, *, reason: str) -> None:
        """Auto-demotion (PRD 灰度产品化): revoke live eligibility.

        Sets run.demoted + account.live_allowed=False so the admission 权限闸
        blocks any redeploy until a manual re-admit.  Never auto-restores.
        """
        uid = _uuid(run_id)
        async with AsyncSessionLocal() as db:
            run = await db.get(LiveRun, uid)
            if run is None or run.demoted:
                return
            run.demoted = True
            db.add(run)
            acct = await db.get(MT5Account, run.account_id)
            if acct is not None:
                acct.live_allowed = False
                db.add(acct)
            await db.commit()
            account_id = run.account_id
        await audit_record(
            "demote", actor=AuditActor(actor_type="system"),
            target_type="live_run", target_id=run_id,
            params={"reason": reason, "account_id": str(account_id)},
            result="blocked",
        )
        await alert_emit(
            "demote", "实盘策略已自动降级",
            f"{reason}。该策略已撤销实盘资格、回退为「仅可模拟」，需在风控中心"
            "人工复核后恢复。",
            level="critical", account_id=account_id, run_id=uid,
        )
        # P0-4 结构化故障输出：降级
        await gate_fault(
            fault_type=FaultType.DEMOTED,
            action_taken="撤销实盘资格，回退为仅可模拟",
            run_id=uid,
            account_id=account_id,
            detail=reason,
        ).emit()

    async def kill_switch(self, run_id: str | None = None) -> None:
        """Close every position of the given run (or all live runs) and stop."""
        ids = [run_id] if run_id else list(self._handles)
        for rid in ids:
            h = self._handles.get(rid)
            if h is not None:
                await self._gateway_close_all(h)
            await self.stop(rid, close_positions=False)
            # P0-4 结构化故障输出：kill switch（原来没有审计/告警）
            if h is not None:
                await gate_fault(
                    fault_type=FaultType.KILL_SWITCH,
                    action_taken="强制清仓并停止策略",
                    run_id=rid,
                ).emit()

    def spawn_kill_all(self, run_ids: list[str]) -> None:
        """Fire-and-forget Kill Switch: real close + stop every listed run.

        Runs directly against the gateway (never through the child RPC path)
        so the whole chain stays within the PRD's ≤3s budget.
        """
        async def _run() -> None:
            for rid in run_ids:
                try:
                    await self.kill_switch(rid)
                except Exception:  # noqa: BLE001 — keep killing the rest
                    logger.exception("kill switch %s failed", rid)
        task = asyncio.create_task(_run(), name="live-kill-switch")
        self._start_tasks.add(task)
        task.add_done_callback(self._start_tasks.discard)

    async def _reclaim(self, run_id: str) -> None:
        uid = _uuid(run_id)
        async with AsyncSessionLocal() as db:
            run = await db.get(LiveRun, uid)
            if run is None:
                return
            run.status = "pending"
            db.add(run)
            await db.commit()
        await self.start(run_id)

    async def _fail(self, run_id: str, code: str, message: str) -> None:
        async with AsyncSessionLocal() as db:
            run = await db.get(LiveRun, _uuid(run_id))
            if run is not None and run.status in ("pending", "running", "paused",
                                                  "stopping"):
                run.status = "failed"
                run.error_code = code[:32]
                run.error_message = message[:8000]
                run.stopped_at = datetime.now(UTC)
                db.add(run)
                await db.commit()


# ── singleton ──────────────────────────────────────────────────
_manager_singleton: Optional[LiveManager] = None


def get_live_manager() -> LiveManager:
    global _manager_singleton
    if _manager_singleton is None:
        _manager_singleton = LiveManager()
    return _manager_singleton
