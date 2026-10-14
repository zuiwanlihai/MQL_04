"""Live runner — long-lived real-money sandbox subprocess (``-m app.sandbox.live_runner``).

Clones :mod:`app.sandbox.paper_runner` but adds the **order RPC channel**: the
strategy's ``ctx.buy/sell/close`` block on an ``order_result`` the parent writes
back over stdin.  The parent interleaves ``bars`` / ``account`` /
``order_result`` on the same stdin stream, so while blocking inside a callback
the child reads every incoming line and parks the non-matching ones in an
inbox deque (single-threaded — a second reader thread on the same buffered
stdin is an interpreter-level race on Windows).

    stdin  {"cmd":"bars","rows":[...]}          → command queue (main loop)
    stdin  {"cmd":"account","account":{...}}    → command queue (main loop)
    stdin  {"cmd":"stop","close":bool}          → command queue (main loop)
    stdin  {"type":"order_result","req_id":..}  → consumed by the waiting RPC

    stdout {"event":"order_request","req_id":..,"op":..,...}

Exit codes mirror paper_runner: 0 = stopped, 2 = safety termination,
3 = strategy load failure, 143 = parent died (stdin EOF beacon).

main() 骨架在 :mod:`app.sandbox.runner_common`；Transport（order RPC +
inbox 停泊）与 account 镜像分支是本文件独有、刻意不共享的部分。
"""

from __future__ import annotations

import os
import sys

# Pin BLAS threading BEFORE numpy is imported (module-level ordering matters).
for _var in (
    "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
    "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
):
    os.environ[_var] = "1"

import json  # noqa: E402
import time  # noqa: E402
from typing import cast  # noqa: E402

from app.sandbox.incremental import IncrementalEngine  # noqa: E402
from app.sandbox.live_engine import LiveEngine  # noqa: E402
from app.sandbox.runner_common import run_incremental_process  # noqa: E402
from app.sandbox.runner_io import RunnerWriter  # noqa: E402

# The child's wait for an order_result must exceed the parent's gateway order
# timeout so the parent always answers first (UNKNOWN_ERROR only if it truly died).
try:
    from app.config import settings as _settings

    RPC_TIMEOUT = float(_settings.LIVE_RPC_TIMEOUT)
except Exception:  # noqa: BLE001 — config import must never kill the runner
    RPC_TIMEOUT = 15.0


def _read_line() -> dict | None:
    """One stdin JSONL message; None on EOF (parent gone → caller exits 143)."""
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            msg = json.loads(text)
        except ValueError:
            continue
        if isinstance(msg, dict):
            return msg


class Transport:
    """JSONL stdout writer + req_id-demultiplexed order RPC (single-threaded)."""

    def __init__(self, writer: RunnerWriter | None = None) -> None:
        self._inbox: list[dict] = []
        self._writer = writer or RunnerWriter()

    @property
    def writer(self) -> RunnerWriter:
        return self._writer

    # ── stdout ───────────────────────────────────────────────────
    def emit(self, kind: str, data: dict) -> None:
        # 心跳线程会并发写 stdout，必须走同一把锁（见 runner_io）。
        self._writer.emit(kind, data)

    def start_heartbeat(self) -> None:
        self._writer.start_heartbeat()

    def stop_heartbeat(self) -> None:
        self._writer.stop_heartbeat()

    # ── order RPC ────────────────────────────────────────────────
    def _take_from_inbox(self, req_id: str) -> dict | None:
        for i, m in enumerate(self._inbox):
            if m.get("type") == "order_result" and m.get("req_id") == req_id:
                return self._inbox.pop(i)
        return None

    def request_order(self, payload: dict) -> dict:
        req_id = os.urandom(12).hex()
        self.emit("order_request", {"req_id": req_id, **payload})
        deadline = time.monotonic() + RPC_TIMEOUT
        while True:
            hit = self._take_from_inbox(req_id)
            if hit is not None:
                return hit
            if time.monotonic() >= deadline:
                # parent never answered → state unknown; reconcile will converge.
                return {
                    "success": False, "error_code": "UNKNOWN_ERROR",
                    "error_message": "下单结果未知（父进程/网关无响应），已转对账收敛",
                }
            msg = _read_line()
            if msg is None:
                os._exit(143)  # stdin EOF — parent is gone
            if msg.get("type") == "order_result":
                if msg.get("req_id") == req_id:
                    return msg
                # stale result for an abandoned request → drop
                continue
            self._inbox.append(msg)  # bars / account / stop parked for later

    def next_command(self) -> dict | None:
        while True:
            while self._inbox:
                m = self._inbox.pop(0)
                if m.get("cmd"):
                    return m
            msg = _read_line()
            if msg is None:
                return None
            if msg.get("cmd"):
                return msg


def main(argv: list[str] | None = None) -> int:
    tp = Transport()

    def _build(payload, bars, storage_path):
        return LiveEngine(payload, bars, tp, storage_path=storage_path)

    def _extra(engine: IncrementalEngine, name: str, cmd: dict) -> bool:
        if name == "account":
            live = cast(LiveEngine, engine)
            live.apply_mirror(cmd.get("account") or {})
            tp.emit("account", live.account_event().data)
            return True
        return False

    return run_incremental_process(
        prog="app.sandbox.live_runner",
        build_engine=_build,
        sink=tp,
        read_command=tp.next_command,
        extra_command=_extra,
        argv=argv,
    )


if __name__ == "__main__":
    sys.exit(main())
