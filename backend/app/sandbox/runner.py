"""Sandbox runner — thin subprocess transport (``python -m app.sandbox.runner``).

The parent (queue supervisor) spawns this module per task with
``--payload <dir>`` holding ``payload.json`` + ``bars.npz``.  It streams
JSONL events on stdout (flushed per line) and communicates the terminal
outcome via exit code:

    0 = done (report emitted)
    2 = safety termination (sandbox violation / callback timeout)
    3 = strategy load failure
    143 = parent died (stdin EOF beacon fired → os._exit)

Determinism: BLAS threading pinned to 1 (env, re-checked here), ``random``
seeded once from the payload, ``PYTHONHASHSEED`` fixed by the parent before
interpreter start.

The stdin EOF beacon is the Windows orphan guard: when the parent process
dies, its write end of our stdin pipe closes and ``read()`` returns b"" —
we exit immediately instead of replaying forever.
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
import threading  # noqa: E402

from app.sandbox.engine import BacktestEngine  # noqa: E402
from app.sandbox.payload import load_payload  # noqa: E402

EXIT_DONE = 0
EXIT_SAFETY = 2
EXIT_LOAD_FAILED = 3
EXIT_PARENT_GONE = 143


def _emit(kind: str, data: dict) -> None:
    line = json.dumps({"event": kind, **data}, ensure_ascii=False, default=str)
    sys.stdout.write(line[:262_144] + "\n")
    sys.stdout.flush()


def _stdin_beacon() -> None:
    """Block on stdin; EOF (parent closed the pipe) ⇒ hard-exit the child."""
    try:
        while True:
            chunk = sys.stdin.buffer.read(1)
            if not chunk:
                break
    except (OSError, ValueError):
        pass
    os._exit(EXIT_PARENT_GONE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.sandbox.runner")
    parser.add_argument("--payload", required=True, help="payload directory")
    args = parser.parse_args(argv)

    beacon = threading.Thread(target=_stdin_beacon, daemon=True, name="stdin-beacon")
    beacon.start()

    payload, bars = load_payload(pathlib.Path(args.payload))
    random.seed(payload.seed)

    engine = BacktestEngine(payload, bars)
    load_failed = False
    safety_failed = False
    for ev in engine.run():
        if ev.kind == "done":
            report = dict(ev.data["report"])
            curve = report.pop("equity_curve", [])
            _emit("equity", {"curve": curve})
            _emit("done", {"report": report})
        elif ev.kind == "fail":
            load_failed = bool(ev.data.get("load_failed"))
            safety_failed = True
            _emit("fail", ev.data)
        else:
            _emit(ev.kind, ev.data)

    if load_failed:
        rc = EXIT_LOAD_FAILED
    elif safety_failed:
        rc = EXIT_SAFETY
    else:
        rc = EXIT_DONE
    sys.stdout.flush()
    # The stdin-beacon daemon thread blocks interpreter finalization
    # (it holds the stdin buffer lock); exit() hard instead.
    os._exit(rc)


if __name__ == "__main__":
    sys.exit(main())
