"""IncrementalEngine — paper/live 共享的「增量驱动」骨架（P2-6 批次一）。

``PaperEngine`` 与 ``LiveEngine`` 都是 M4 :class:`BacktestEngine` 之上的薄
适配器：装载策略一次、回放启动历史（warmup 仍拒单），此后由父进程经 stdin
逐根喂**新收盘** bar / 实时 tick。整段循环——storage 持久化、锚点重置、
feed、feed_ticks、start 回放、stop 收尾——两者逐字节相同，收集于此。

逐子类差异全部是**具名钩子**（而非注释提示）：
  - ``_create_ctx``      paper 的 ``PaperContext(eng)`` vs live 的
                         ``LiveContext(self)``（构造参数不同）
  - ``_before_replay``   live 在回放前 pin ``eng.warmup_end`` → 历史永不成交
  - ``_after_replay``    live 在 ``live=True`` 后解 pin → on_tick 可交易
  - ``_close_on_stop``   paper 本地强平；live 真实平仓由父进程执行
  - ``account_event``    载荷契约两端不同（paper 4 位小数 + asdict 持仓 vs
                         live 原始 broker 镜像 dict）且原样持久化进
                         run.positions / equity_history → 前端契约，不统一

状态恢复契约（PRD §状态恢复）：只有 ``ctx.storage`` 跨重启存活，每处理一根
bar 后持久化到 payload 目录的 ``storage.json``。本类**没有**回测式的收盘强平：
仓位持有到用户停止（可选平仓）或触发止损为止。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Optional

import numpy as np

from app.sandbox import engine as engine_mod
from app.sandbox.context import BacktestContext
from app.sandbox.engine import BacktestEngine, EngineEvent
from app.sandbox.import_hook import SandboxViolationError
from app.sandbox.payload import BacktestPayload, BarArrays


def _iso(epoch_s: int) -> str:
    return datetime.fromtimestamp(int(epoch_s), tz=UTC).isoformat()


class IncrementalEngine(ABC):
    """Incremental loop base; signal machinery all delegated to BacktestEngine."""

    def __init__(
        self,
        payload: BacktestPayload,
        bars: BarArrays,
        storage_path: Optional[Path] = None,
    ) -> None:
        self.payload = payload
        self.eng = BacktestEngine(payload, bars)
        self.ctx: BacktestContext = self._create_ctx()
        self.eng.ctx = self.ctx
        self.storage_path = storage_path
        self.live = False

    # ── per-subclass hooks ─────────────────────────────────────

    @abstractmethod
    def _create_ctx(self) -> BacktestContext:
        """SDK 上下文：paper 传 ``PaperContext(self.eng)``、live 传
        ``LiveContext(self)``——live 的上下文要回读镜像与 RPC 通道，构造参数
        不同，所以必然是钩子。"""

    def _before_replay(self, eng: BacktestEngine) -> None:
        """回放启动历史前的钩子；默认 no-op。

        live 覆写：把 ``warmup_end`` pin 到全部启动历史，回放期任何下单都被
        WARMUP_PERIOD 拒绝——历史永不成交。"""

    def _after_replay(self, eng: BacktestEngine) -> None:
        """回放结束（``live=True``）后的钩子；默认 no-op。

        live 覆写：解 pin 到最后一根回放 bar——tick 不推进 bar 索引，若不复位
        on_tick 将永久被拒。"""

    @abstractmethod
    def _close_on_stop(self, eng: BacktestEngine) -> Iterator[EngineEvent]:
        """``close_positions=True`` 时的收尾动作。

        paper：在最后一根 bar 收盘价本地强平。
        live：空迭代——真实平仓由父进程执行（RPC → 网关），子进程不成交。"""

    @abstractmethod
    def account_event(self) -> EngineEvent:
        """账户快照事件；载荷契约两端不同（见模块 docstring），不统一。"""

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

    # ── lifecycle ──────────────────────────────────────────────

    def start(self) -> Iterator[EngineEvent]:
        """Load strategy → on_init → replay startup history (no forced close)."""
        eng = self.eng
        self._before_replay(eng)
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
        self._after_replay(eng)

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

        Orders placed inside on_tick go through the same order path as on_bar
        (paper: local fill; live: parent risk gate → gateway RPC).  Batch-end
        persistence: storage/account snapshot/drain once per batch, not per
        tick (ticks can arrive at tens per second).
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
        """Terminate: optionally force-close at last close (paper only),
        call on_stop, persist storage, emit the final account snapshot."""
        eng = self.eng
        if close_positions:
            yield from self._close_on_stop(eng)
        try:
            eng._call("on_stop", eng.ctx, "stopped",
                      timeout=eng.payload.callback_timeout_seconds)
        except Exception:
            pass
        eng.phase = "stop"
        self._persist_storage()
        yield self.account_event()
        yield from eng._drain()
