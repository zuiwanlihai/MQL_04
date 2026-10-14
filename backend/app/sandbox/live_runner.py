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

import argparse  # noqa: E402
import json  # noqa: E402
import pathlib  # noqa: E402
import random  # noqa: E402
import time  # noqa: E402
from collections import deque  # noqa: E402

from app.sandbox.live_engine import LiveEngine  # noqa: E402
from app.sandbox.payload import load_payload  # noqa: E402

EXIT_DONE = 0
EXIT_SAFETY = 2
EXIT_LOAD_FAILED = 3

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

    def __init__(self) -> None:
        self._inbox: deque[dict] = deque()

    # ── stdout ───────────────────────────────────────────────────
    def emit(self, kind: str, data: dict) -> None:
        line = json.dumps({"event": kind, **data}, ensure_ascii=False, default=str)
        sys.stdout.write(line[:262_144] + "\n")
        sys.stdout.flush()

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
                m = self._inbox.popleft()
                if m.get("cmd"):
                    return m
            msg = _read_line()
            if msg is None:
                return None
            if msg.get("cmd"):
                return msg


def _emit_all(tp: Transport, events) -> dict | None:
    fail: dict | None = None
    for ev in events:
        if ev.kind == "fail":
            fail = ev.data
            tp.emit("fail", ev.data)
        else:
            tp.emit(ev.kind, ev.data)
    return fail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.sandbox.live_runner")
    parser.add_argument("--payload", required=True, help="payload directory")
    args = parser.parse_args(argv)

    payload_dir = pathlib.Path(args.payload)
    payload, bars = load_payload(payload_dir)
    random.seed(payload.seed)

    tp = Transport()
    live = LiveEngine(payload, bars, tp, storage_path=payload_dir / "storage.json")
    live.restore_storage()

    fail = _emit_all(tp, live.start())
    if fail is not None:
        return EXIT_LOAD_FAILED if fail.get("load_failed") else EXIT_SAFETY
    tp.emit("started", {})  # parent-side gate: ticks may be delivered from now on

    while True:
        cmd = tp.next_command()
        if cmd is None:
            os._exit(143)  # stdin EOF — parent is gone
        name = cmd.get("cmd")
        if name == "bars":
            if _emit_all(tp, live.feed(list(cmd.get("rows") or []))) is not None:
                return EXIT_SAFETY
        elif name == "ticks":
            if _emit_all(tp, live.feed_ticks(list(cmd.get("rows") or []))) is not None:
                return EXIT_SAFETY
        elif name == "account":
            live.apply_mirror(cmd.get("account") or {})
            tp.emit("account", live.account_event().data)
        elif name == "status":
            tp.emit("account", live.account_event().data)
        elif name == "reset_anchor":
            new = live.reset_anchor()
            if new is None:
                live.eng.emit_log("warn", "重置锚点失败：暂无可用报价")
            else:
                info = live.eng.symbol_info
                digits = info.digits if info is not None else 5
                live.eng.emit_log(
                    "info", f"网格锚点已重置为当前价 {new:.{digits}f}"
                )
            _emit_all(tp, live.eng._drain())
        elif name == "stop":
            _emit_all(tp, live.stop(bool(cmd.get("close", True))))
            sys.stdout.flush()
            return EXIT_DONE


if __name__ == "__main__":
    sys.exit(main())
