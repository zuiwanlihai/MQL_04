"""Paper runner — long-lived sandbox subprocess (``python -m app.sandbox.paper_runner``).

Spawned once per paper run by the manager with ``--payload <dir>`` holding
``payload.json`` + ``bars.npz`` (startup history) + ``storage.json`` (optional,
recovery contract).  Streams JSONL events on stdout and consumes JSONL
commands on stdin:

    {"cmd":"bars","rows":[[t,o,h,l,c,v,sp],...]}   feed newly closed bars
    {"cmd":"status"}                               emit account snapshot now
    {"cmd":"stop","close":true}                    final snapshot → exit 0

Exit codes mirror the backtest runner: 0 = stopped, 2 = safety termination,
3 = strategy load failure, 143 = parent died (stdin EOF beacon).

Unlike the backtest runner this process BLOCKS on stdin between commands —
the parent owns the gateway polling loop and only delivers closed bars.
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

from app.sandbox.paper_engine import PaperEngine  # noqa: E402
from app.sandbox.payload import load_payload  # noqa: E402
from app.sandbox.runner_io import RunnerWriter, start_wedge_reaper  # noqa: E402

EXIT_DONE = 0
EXIT_SAFETY = 2
EXIT_LOAD_FAILED = 3

_writer = RunnerWriter()


def _emit(kind: str, data: dict) -> None:
    _writer.emit(kind, data)


def _emit_all(events) -> dict | None:
    """Drain an engine event iterator onto stdout; return the fail dict or None."""
    fail: dict | None = None
    for ev in events:
        if ev.kind == "fail":
            fail = ev.data
            _emit("fail", ev.data)
        else:
            _emit(ev.kind, ev.data)
    return fail


def _read_command() -> dict | None:
    """One JSONL stdin command; None on EOF (parent gone → caller exits 143)."""
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            cmd = json.loads(text)
        except ValueError:
            continue
        if isinstance(cmd, dict) and cmd.get("cmd"):
            return cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.sandbox.paper_runner")
    parser.add_argument("--payload", required=True, help="payload directory")
    args = parser.parse_args(argv)

    # 心跳必须早于任何可能挂住的步骤启动，否则父进程会把启动耗时误判成卡死。
    _writer.start_heartbeat()

    payload_dir = pathlib.Path(args.payload)
    payload, bars = load_payload(payload_dir)
    random.seed(payload.seed)

    paper = PaperEngine(payload, bars, storage_path=payload_dir / "storage.json")
    paper.restore_storage()
    start_wedge_reaper(_writer, paper.eng.unresolved_timeout_seconds)

    # start: load → on_init → replay startup history
    fail = _emit_all(paper.start())
    if fail is not None:
        return EXIT_LOAD_FAILED if fail.get("load_failed") else EXIT_SAFETY
    _emit("started", {})  # parent-side gate: ticks may be delivered from now on

    while True:
        cmd = _read_command()
        if cmd is None:
            os._exit(143)  # stdin EOF — parent is gone
        name = cmd.get("cmd")
        if name == "bars":
            if _emit_all(paper.feed(list(cmd.get("rows") or []))) is not None:
                return EXIT_SAFETY
        elif name == "ticks":
            if _emit_all(paper.feed_ticks(list(cmd.get("rows") or []))) is not None:
                return EXIT_SAFETY
        elif name == "status":
            _emit("account", paper.account_event().data)
        elif name == "reset_anchor":
            new = paper.reset_anchor()
            if new is None:
                paper.eng.emit_log("warn", "重置锚点失败：暂无可用报价")
            else:
                info = paper.eng.symbol_info
                digits = info.digits if info is not None else 5
                paper.eng.emit_log(
                    "info", f"网格锚点已重置为当前价 {new:.{digits}f}"
                )
            _emit_all(paper.eng._drain())
        elif name == "stop":
            _emit_all(paper.stop(bool(cmd.get("close", True))))
            _writer.stop_heartbeat()
            sys.stdout.flush()
            return EXIT_DONE


if __name__ == "__main__":
    sys.exit(main())
