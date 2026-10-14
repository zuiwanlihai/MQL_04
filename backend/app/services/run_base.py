"""Shared base class for paper and live run managers.

P2-6 extraction (2026-10-14): byte-for-byte identical methods between
``services/paper/manager.py`` and ``services/live/manager.py`` — they live here
now.  Each subclass keeps all trading/DB/model-specific code in its own module.

Extracted (zero-diff) methods:
  - ``__init__``      handles dict + GC-guard task set
  - ``spawn_start``   fire-and-forget start() with strong task ref
  - ``active_count``  returns len(_handles)
  - ``shutdown``      cancel stream/poll/tick tasks, wait ≤10s for children
  - ``pause`` / ``resume`` / ``reset_anchor``  control plane (via ``run_model``)
  - ``_tick_poll``    on_tick feed loop; the only cross-subclass differences go
                      through the ``_ticks_hold`` / ``_gateway_client_for`` hooks
  - ``_reclaim``      re-spawn a run whose process died

Extracted (zero-diff) module-level helpers: ``_uuid`` / ``_dec`` / ``_dt`` /
``_chunks`` / ``_select_history`` / ``_make_singleton`` and the constants
``_FLUSH_ROWS`` / ``_ACCOUNT_WRITE_INTERVAL``.

Subclass contract (the ONLY differences the extracted code needs):
  - ``kind``               "paper" / "live" — logger name + log-line prefix
  - ``run_model``          PaperRun / LiveRun — control-plane row model
  - ``_ticks_hold``        extra gate before tick delivery (live: order RPC busy)
  - ``_gateway_client_for`` constructs the tick-feed client using the SUBCLASS
                           module's ``GatewayClient`` global (see docstring)

Concrete subclasses MUST override (not extracted — model/DB/behavior differ):
  - ``start``          orchestration differs wildly (paper = sandbox subprocess,
                       live = real gateway RPC risk-gated)
  - ``startup_sweep``  SQLAlchemy model differs (PaperRun vs LiveRun)
  - ... all other trading methods

Generic type parameter ``HandleT`` lets mypy infer the concrete ``_RunHandle``
from each subclass without duplicating the ``__init__`` body.

See also: ``services/paper/manager.py``, ``services/live/manager.py``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid as uuid_mod
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from decimal import Decimal
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    Generic,
    Optional,
    Protocol,
    TypeVar,
)

from sqlalchemy import select, update

from app.config import settings
from app.engine.session import AsyncSessionLocal
from app.sandbox.payload import BarArrays
from app.services.gateway_client import GatewayError
from app.services.tick_feed import fetch_ea_buffer_ticks, fetch_gateway_ticks

if TYPE_CHECKING:
    from app.services.backtest.supervisor import RunnerSupervisor
    from app.services.gateway_client import GatewayClient

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


def _chunks(seq: list, size: int) -> list[list]:
    """Split ``seq`` into consecutive chunks of at most ``size`` items."""
    return [seq[i:i + size] for i in range(0, len(seq), size)]


def _select_history(rows: list[dict], tf: int, now_epoch: int, need: int) -> list[dict]:
    """Keep only CLOSED bars (``time + tf <= now``) and trim to the last ``need``.

    The gateway's "latest N" endpoint can return a still-forming live bar;
    feeding it at startup would either be skipped by the poll cursor or fill
    a signal before its period has elapsed.  Weekend/holiday gaps are handled
    upstream by the endpoint's fallback, so we never drop bars for being old.
    """
    closed = [r for r in rows if int(r["time"]) + tf <= now_epoch]
    return closed[-need:] if need > 0 else []


_SingletonT = TypeVar("_SingletonT")


def _make_singleton(factory: Callable[[], _SingletonT]) -> Callable[[], _SingletonT]:
    """Lazy module-level singleton getter (same semantics as the prior global)."""
    cache: list[_SingletonT] = []

    def get() -> _SingletonT:
        if not cache:
            cache.append(factory())
        return cache[0]

    return get


class RunHandleLike(Protocol):
    """Fields the extracted base methods touch on a run handle.

    Both concrete ``_RunHandle`` dataclasses (paper/live) satisfy this
    structurally; the live one additionally carries ``busy`` (read by
    ``LiveManager._ticks_hold``).
    """

    sup: "RunnerSupervisor"
    stream_task: asyncio.Task
    poll_task: asyncio.Task
    tick_task: Optional[asyncio.Task]
    paused: asyncio.Event
    stopping: bool
    child_live: bool
    node_type: str
    gateway_url: str
    ea_node_id: str
    symbol: str
    last_tick_msc: int


HandleT = TypeVar("HandleT", bound=RunHandleLike)


class BaseRunManager(ABC, Generic[HandleT]):
    """Infrastructure-only base.  Subclassed by PaperManager / LiveManager."""

    kind: ClassVar[str]  # "paper" / "live" — logger name + log-line prefix
    run_model: ClassVar[type[Any]]  # PaperRun / LiveRun — control-plane row

    def __init__(self) -> None:
        self._handles: dict[str, HandleT] = {}
        self._start_tasks: set[asyncio.Task] = set()

    @property
    def _log(self) -> logging.Logger:
        return logging.getLogger(f"mt5quant.{self.kind}.manager")

    def spawn_start(self, run_id: str) -> None:
        """Fire-and-forget start with a strong task reference (GC guard)."""
        task = asyncio.get_running_loop().create_task(self.start(run_id))
        self._start_tasks.add(task)
        task.add_done_callback(self._start_tasks.discard)

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
        self._log.info("%s manager stopped", self.kind)

    # ── control plane ──────────────────────────────────────────

    async def pause(self, run_id: str) -> str:
        model = self.run_model
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                update(model)
                .where(model.id == _uuid(run_id), model.status == "running")
                .values(status="paused")
            )
            await db.commit()
            if not res.rowcount:  # type: ignore[attr-defined]  # SQLAlchemy Result type annotation gap — real type has .rowcount/.scalars()
                row = await db.scalar(select(model.status).where(
                    model.id == _uuid(run_id)))
                return row or "not_found"
        h = self._handles.get(run_id)
        if h is not None:
            h.paused.set()
        return "paused"

    async def resume(self, run_id: str) -> str:
        model = self.run_model
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                update(model)
                .where(model.id == _uuid(run_id), model.status == "paused")
                .values(status="running")
            )
            await db.commit()
            if not res.rowcount:  # type: ignore[attr-defined]  # SQLAlchemy Result type annotation gap — real type has .rowcount/.scalars()
                row = await db.scalar(select(model.status).where(
                    model.id == _uuid(run_id)))
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

    async def _reclaim(self, run_id: str) -> None:
        """Re-spawn a run whose process died (restart-orphan resume path)."""
        uid = _uuid(run_id)
        async with AsyncSessionLocal() as db:
            run = await db.get(self.run_model, uid)
            if run is None:
                return
            run.status = "pending"
            db.add(run)
            await db.commit()
        await self.start(run_id)

    @staticmethod
    def _rows_to_arrays(rows: list[dict]) -> BarArrays:
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

    # ── tick poll task (on_tick feed, SDK v1.1.0) ──────────────

    def _ticks_hold(self, h: HandleT) -> bool:
        """Extra per-tick gate; default never holds (live: order RPC in flight)."""
        return False

    @abstractmethod
    def _gateway_client_for(self, h: HandleT) -> Optional["GatewayClient"]:
        """Construct the tick-feed client using the SUBCLASS module's global.

        It MUST be implemented in the subclass's own module (not here): tests
        monkeypatch ``app.services.paper.manager.GatewayClient`` /
        ``app.services.live.manager.GatewayClient``, and the name has to be
        looked up in that module at call time for the patch to take effect.
        EA-bridge nodes feed from the registry buffer instead — no client.
        """

    async def _tick_poll(self, run_id: str) -> None:
        h = self._handles.get(run_id)
        assert h is not None
        is_ea = h.node_type == "ea_bridge"
        client = self._gateway_client_for(h)
        try:
            while True:
                await asyncio.sleep(settings.TICK_POLL_SECONDS)
                cur = self._handles.get(run_id)
                if cur is not h:
                    return  # handle replaced/removed
                if not h.child_live or h.paused.is_set() or h.stopping:
                    continue  # warmup replay / paused / stopping → no ticks
                if self._ticks_hold(h):
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
                    self._log.warning(
                        "%s %s tick poll failed: %s", self.kind, run_id, exc.detail
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
            self._log.exception("%s tick poll %s crashed", self.kind, run_id)
        finally:
            if client is not None:
                await client.aclose()

    @abstractmethod
    async def start(self, run_id: str) -> None:  # pragma: no cover — interface
        """Concrete orchestration (differs wildly between paper/live)."""

    @abstractmethod
    async def startup_sweep(self) -> None:  # pragma: no cover — interface
        """Mark rows orphaned by backend restart as failed (model differs)."""

    def active_count(self) -> int:
        return len(self._handles)
