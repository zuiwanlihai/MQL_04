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

main() 骨架在 :mod:`app.sandbox.runner_common`；本文件只留 paper 差异：
模块级 writer（无 RPC 通道）与丢弃非 cmd 行的 stdin 契约。
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

from app.sandbox.paper_engine import PaperEngine  # noqa: E402
from app.sandbox.runner_common import run_incremental_process  # noqa: E402
from app.sandbox.runner_io import RunnerWriter  # noqa: E402

_writer = RunnerWriter()


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
    return run_incremental_process(
        prog="app.sandbox.paper_runner",
        build_engine=lambda payload, bars, storage_path: PaperEngine(
            payload, bars, storage_path=storage_path
        ),
        sink=_writer,
        read_command=_read_command,
        argv=argv,
    )


if __name__ == "__main__":
    sys.exit(main())
