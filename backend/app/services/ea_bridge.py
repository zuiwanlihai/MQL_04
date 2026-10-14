"""EA bridge registry (P4 方案 B, EB-1).

In-process session state for EA bridge nodes — the EA talks to the backend
over WebRequest(HTTPS) (``POST /api/v1/ea/sync``), so there is no persistent
connection: this registry buffers what arrives between syncs and hands it to
the paper/live managers' tick poll, plus a req_id→Future command channel for
downstream RPCs (rates_req EB-2, order_request EB-3).

Layout per node:
  * ``ticks[symbol]``  — bounded deque of ``(time_msc, bid, ask)``; capacity
    ``EA_TICK_BUFFER_MAX`` with drop-oldest + a counted warning.
  * ``commands``       — pending command dicts drained by the next sync
    (``close_all``/kill-switch style commands may jump the queue via ``front``).
  * ``futures[req_id]``— resolved by ``POST /ea/result``; awaited with
    ``EA_RESULT_TIMEOUT`` (30 s) after which they fail automatically.

Buffer reads are destructive (``take_ticks``): the run-side cursor
(``last_tick_msc``) still governs dedup/skip semantics exactly as for python
gateway nodes, so a paused/warming-up run simply leaves rows in the buffer
until capacity trims them.
"""

from __future__ import annotations

import asyncio
import logging
import uuid as uuid_mod
from collections import deque
from dataclasses import dataclass, field

from app.config import settings

logger = logging.getLogger("mt5quant.ea_bridge")

COMMAND_QUEUE_MAX = 100  # per node — orders arrive post-risk-gate, never bursts
EA_RATES_MAX_BARS = 5000  # 单次 rates RPC 返回上限（与 EA 侧分页上限一致）


@dataclass
class EASession:
    node_id: str
    last_seen: float = 0.0
    ticks: dict[str, deque] = field(default_factory=dict)  # symbol → rows
    dropped: dict[str, int] = field(default_factory=dict)  # symbol → trimmed count
    commands: deque = field(default_factory=deque)
    futures: dict[str, asyncio.Future] = field(default_factory=dict)
    last_account: dict | None = None  # latest /ea/sync account snapshot (EB-2)


class EARegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, EASession] = {}

    # ── sessions ───────────────────────────────────────────────

    def session(self, node_id: str) -> EASession:
        sess = self._sessions.get(node_id)
        if sess is None:
            sess = EASession(node_id=node_id)
            self._sessions[node_id] = sess
        return sess

    def drop_session(self, node_id: str) -> None:
        sess = self._sessions.pop(node_id, None)
        if sess is not None:
            for fut in sess.futures.values():
                if not fut.done():
                    fut.cancel()

    def reset(self) -> None:
        """Clear all sessions (tests)."""
        for sess in self._sessions.values():
            for fut in sess.futures.values():
                if not fut.done():
                    fut.cancel()
        self._sessions.clear()

    def touch(self, node_id: str, ts: float) -> None:
        self.session(node_id).last_seen = ts

    def last_seen(self, node_id: str) -> float:
        sess = self._sessions.get(node_id)
        return sess.last_seen if sess is not None else 0.0

    # ── tick buffer (上行) ─────────────────────────────────────

    def push_ticks(
        self, node_id: str, symbol: str, rows: list[list], *, max_rows: int,
    ) -> int:
        """Normalize + buffer ``rows``; returns accepted count.

        Malformed rows are skipped; overflow beyond ``EA_TICK_BUFFER_MAX``
        silently drops the oldest (deque maxlen) and counts a warning.
        """
        clean: list[tuple[int, float, float]] = []
        for r in rows[:max_rows]:
            try:
                clean.append((int(r[0]), float(r[1]), float(r[2])))
            except (IndexError, TypeError, ValueError):
                continue
        sess = self.session(node_id)
        dq = sess.ticks.get(symbol)
        if dq is None:
            dq = deque(maxlen=settings.EA_TICK_BUFFER_MAX)
            sess.ticks[symbol] = dq
        before = len(dq)
        dq.extend(clean)
        dropped = before + len(clean) - len(dq)
        if dropped:
            sess.dropped[symbol] = sess.dropped.get(symbol, 0) + dropped
            logger.warning(
                "ea buffer overflow node=%s symbol=%s dropped=%d (total=%d)",
                node_id, symbol, dropped, sess.dropped[symbol],
            )
        return len(clean)

    def take_ticks(self, node_id: str, symbol: str) -> list[list]:
        """Destructively drain the buffer; caller filters by its own cursor."""
        sess = self._sessions.get(node_id)
        dq = sess.ticks.get(symbol) if sess is not None else None
        if not dq:
            return []
        rows = [list(r) for r in dq]
        dq.clear()
        return rows

    def dropped_count(self, node_id: str, symbol: str) -> int:
        sess = self._sessions.get(node_id)
        return sess.dropped.get(symbol, 0) if sess is not None else 0

    def set_account(self, node_id: str, account: dict) -> None:
        self.session(node_id).last_account = account

    def last_account(self, node_id: str) -> dict | None:
        sess = self._sessions.get(node_id)
        return sess.last_account if sess is not None else None

    # ── command queue (下行) ───────────────────────────────────

    def enqueue_command(
        self, node_id: str, cmd_type: str, params: dict | None = None,
        *, front: bool = False,
    ) -> str:
        """Queue a command for the EA; returns its req_id.

        ``front=True`` inserts at the head (kill-switch / close_all must not
        wait behind queued rates/orders).
        """
        sess = self.session(node_id)
        req_id = uuid_mod.uuid4().hex
        cmd = {"type": cmd_type, "req_id": req_id, "params": params or {}}
        if front:
            sess.commands.appendleft(cmd)
            # keep the queue bounded even with front inserts
            while len(sess.commands) > COMMAND_QUEUE_MAX:
                sess.commands.pop()
        else:
            sess.commands.append(cmd)
            while len(sess.commands) > COMMAND_QUEUE_MAX:
                sess.commands.popleft()
        return req_id

    def drain_commands(self, node_id: str) -> list[dict]:
        sess = self.session(node_id)
        if not sess.commands:
            return []
        cmds = list(sess.commands)
        sess.commands.clear()
        return cmds

    # ── req_id RPC futures ─────────────────────────────────────

    def new_future(self, node_id: str, req_id: str) -> asyncio.Future:
        sess = self.session(node_id)
        old = sess.futures.pop(req_id, None)
        if old is not None and not old.done():
            old.cancel()
        fut = asyncio.get_running_loop().create_future()
        sess.futures[req_id] = fut
        return fut

    def resolve_result(
        self, node_id: str, req_id: str, ok: bool,
        payload: dict | None, error: str | None,
    ) -> bool:
        """Deliver /ea/result to a pending future; False = stale/unknown."""
        sess = self._sessions.get(node_id)
        fut = sess.futures.pop(req_id, None) if sess is not None else None
        if fut is None or fut.done():
            return False
        fut.set_result({"ok": ok, "payload": payload or {}, "error": error})
        return True

    async def wait_result(self, node_id: str, req_id: str) -> dict:
        """Await a command result; timeout auto-fails (plan §4, 30 s)."""
        sess = self._sessions.get(node_id)
        fut = sess.futures.get(req_id) if sess is not None else None
        if fut is None:
            return {"ok": False, "payload": {}, "error": "UNKNOWN_REQ"}
        try:
            return await asyncio.wait_for(fut, timeout=settings.EA_RESULT_TIMEOUT)
        except asyncio.TimeoutError:
            sess.futures.pop(req_id, None)  # type: ignore[union-attr]
            return {"ok": False, "payload": {}, "error": "RESULT_TIMEOUT"}


async def request_ea_rates(
    node_id: str, symbol: str, timeframe: str,
    *, count: int = 0, start: int = 0, end: int = 0,
) -> dict:
    """rates RPC（EB-2）：入队 ``rates_req`` → EA ``CopyRates`` 应答。

    两种语义（切窗逻辑留在后端，EA 只按请求范围返回）：
      * ``count > 0`` → 最新 N 根（启动快照；禁固定日历窗——休市坑，
        CopyRates 按根数取天然落在最近有数据的 bar 上）；
      * ``start``/``end`` → ``[start, end)`` epoch 秒区间（poll 补数）。

    返回 ``wait_result`` dict：``{ok, payload:{bars:[[t,o,h,l,c,vol,spread],…],
    symbol_info:{…}}, error}``。bars 的 time 与 python 网关 rates 路径同域
    （MT5 服务器时区标签秒），后端游标语义两端一致。
    """
    params: dict = {"symbol": symbol.upper(), "timeframe": timeframe}
    if count > 0:
        params["count"] = min(int(count), EA_RATES_MAX_BARS)
    else:
        params["start"] = int(start)
        params["end"] = int(end)
    # 入队 + 建 future 之间无 await —— 对其他协程原子，EA 再快也抢不跑。
    req_id = registry.enqueue_command(node_id, "rates_req", params)
    registry.new_future(node_id, req_id)
    return await registry.wait_result(node_id, req_id)


registry = EARegistry()
