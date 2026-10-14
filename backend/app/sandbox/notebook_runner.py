"""Notebook cell runner — subprocess transport (``python -m app.sandbox.notebook_runner``).

Mirrors the backtest runner protocol (payload dir, JSONL stdout events,
stdin EOF beacon, exit codes).  One process per cell:

    restore state.pkl → restricted exec of cell.py → capture outputs
    → save state.pkl → emit done → exit 0

Payload layout (written by the parent kernel service):

    cell.py          cell source (UTF-8)
    state.pkl        pickled globals from the previous cell (optional)
    bars/{key}.json  pre-fetched OHLCV rows for literal get_bars calls
    meta.json        {"bars": {key: {"symbol","timeframe","count"}}}

Exit codes: 0 = done, 2 = sandbox violation, 3 = load failure,
143 = parent gone (stdin EOF beacon).

Data flow note: the sandbox forbids sockets, so ``get_bars`` cannot reach
the gateway from inside the cell.  The parent AST-scans the cell for
literal ``get_bars(...)`` calls, pre-fetches every dataset, and the
injected ``get_bars`` serves rows from ``bars/*.json``.
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
import ast  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import pathlib  # noqa: E402
import pickle  # noqa: E402
import threading  # noqa: E402
import traceback  # noqa: E402

# Pre-import pandas/numpy BEFORE the sandbox installs its import finder and
# escape guards — a cold pandas import under the hook hangs (same proven
# ordering as app.sandbox.runner, whose engine chain imports pandas at
# module level prior to install_all()).
import numpy  # noqa: F401,E402
import pandas  # noqa: F401,E402

from app.sandbox.import_hook import (  # noqa: E402
    SandboxViolationError,
    install_all,
    make_restricted_builtins,
    uninstall_all,
)

EXIT_DONE = 0
EXIT_SAFETY = 2
EXIT_LOAD_FAILED = 3
EXIT_PARENT_GONE = 143

MAX_STDOUT_BYTES = 262_144
MAX_REPR_CHARS = 8_192
MAX_TRACEBACK_CHARS = 8_192
MAX_CHART_POINTS = 5_000
MAX_CHARTS = 4


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


# ── injected helpers (present in cell globals, stripped before pickling) ──

def _make_get_bars(bars_dir: pathlib.Path, meta: dict) -> object:
    def get_bars(symbol: str, timeframe: str = "H1", count: int = 500):
        """Pre-fetched OHLCV rows as a pandas DataFrame (literal args only)."""
        import pandas as pd

        key = f"{str(symbol).upper()}_{str(timeframe).upper()}_{int(count)}"
        path = bars_dir / f"{key}.json"
        if not path.exists():
            raise RuntimeError(
                f"get_bars({symbol!r}, {timeframe!r}, {count!r}) 未预取："
                "参数必须是字面量常量（变量调用请在上一行先定义常量后再传入）"
            )
        rows = json.loads(path.read_text(encoding="utf-8"))
        df = pd.DataFrame(rows)
        if "time" in df.columns and len(df) and pd.api.types.is_numeric_dtype(df["time"]):
            # epoch seconds → tz-aware UTC datetime for research ergonomics
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    return get_bars


def _to_points(values) -> list:  # noqa: ANN001 — duck-typed pandas/numpy input
    import pandas as pd

    s = pd.Series(values)
    pts: list = []
    for idx, val in s.items():
        if isinstance(idx, pd.Timestamp):
            t = idx.isoformat()
        else:
            t = str(idx)
        try:
            v = float(val)
        except (TypeError, ValueError):
            v = None
        pts.append({"time": t, "value": v})
    if len(pts) > MAX_CHART_POINTS:
        step = len(pts) / MAX_CHART_POINTS
        pts = [pts[int(i * step)] for i in range(MAX_CHART_POINTS)]
    return pts


def _make_plot(charts: list) -> object:
    def plot(data, columns=None, title: str = "") -> None:  # noqa: ANN001
        """Render a line chart in the notebook output (no matplotlib needed)."""
        import pandas as pd

        if len(charts) >= MAX_CHARTS:
            raise RuntimeError(f"每个 cell 最多输出 {MAX_CHARTS} 张图表")
        series: list = []
        if isinstance(data, pd.DataFrame):
            cols = list(columns) if columns else list(data.columns)
            for col in cols:
                series.append({"name": str(col), "points": _to_points(data[col])})
        else:
            name = title or "series"
            series.append({"name": str(name), "points": _to_points(data)})
        charts.append({"title": str(title), "series": series})
        _emit("chart", {"chart": charts[-1]})

    return plot


def _injected(bars_dir: pathlib.Path, meta: dict, charts: list) -> dict:
    return {
        "get_bars": _make_get_bars(bars_dir, meta),
        "plot": _make_plot(charts),
    }


_INJECTED_NAMES = ("get_bars", "plot")


def _clean_globals(globals_dict: dict) -> dict:
    """Strip exec-protocol keys so the state pickle only holds user data."""
    out = {}
    for k, v in globals_dict.items():
        if k.startswith("__") or k in _INJECTED_NAMES:
            continue
        out[k] = v
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.sandbox.notebook_runner")
    parser.add_argument("--payload", required=True, help="payload directory")
    args = parser.parse_args(argv)

    beacon = threading.Thread(target=_stdin_beacon, daemon=True, name="stdin-beacon")
    beacon.start()

    payload = pathlib.Path(args.payload)
    bars_dir = payload / "bars"
    meta: dict = {}
    meta_path = payload / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}

    # ── restore session state (trusted: written by a previous run of ours) ──
    globals_dict: dict = {}
    state_path = payload / "state.pkl"
    if state_path.exists():
        try:
            with state_path.open("rb") as fh:
                globals_dict = pickle.load(fh)  # noqa: S301 — own trusted snapshot
        except Exception:
            globals_dict = {}

    source = (payload / "cell.py").read_text(encoding="utf-8")

    # Parse before sandboxing so syntax errors are a clean load failure.
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        _emit(
            "error",
            {
                "etype": "SyntaxError",
                "message": exc.msg,
                "traceback": f"line {exc.lineno}: {exc.msg}",
            },
        )
        return EXIT_LOAD_FAILED

    # Jupyter semantics: a trailing bare expression is the cell "result".
    result_repr = None
    body = tree.body
    tail_expr = None
    if body and isinstance(body[-1], ast.Expr):
        tail_expr = body[-1]
        body = body[:-1]
    exec_source = ast.Module(body=body, type_ignores=[])
    try:
        compiled = compile(exec_source, "<notebook>", "exec")
    except SyntaxError as exc:
        _emit(
            "error",
            {
                "etype": "SyntaxError",
                "message": exc.msg,
                "traceback": f"line {exc.lineno}: {exc.msg}",
            },
        )
        return EXIT_LOAD_FAILED
    tail_compiled = None
    if tail_expr is not None:
        try:
            # compile(mode="eval") requires an ast.Expression node, not Module.
            tail_compiled = compile(
                ast.copy_location(ast.Expression(body=tail_expr.value), tail_expr),
                "<notebook>",
                "eval",
            )
        except SyntaxError:  # pragma: no cover — parse already succeeded
            tail_compiled = None

    # ── sandbox on ────────────────────────────────────────────────────────
    install_all()
    try:
        charts: list = []
        globals_dict.setdefault("__builtins__", make_restricted_builtins())
        globals_dict["__builtins__"] = make_restricted_builtins()
        globals_dict["__name__"] = "__notebook__"
        globals_dict["__file__"] = "<notebook>"
        for name, fn in _injected(bars_dir, meta, charts).items():
            globals_dict[name] = fn

        captured = io.StringIO()
        stdout_lock = threading.Lock()
        emitted_bytes = 0

        class _Tee(io.TextIOBase):
            def write(self, s: str) -> int:  # type: ignore[override]
                nonlocal emitted_bytes
                with stdout_lock:
                    if emitted_bytes < MAX_STDOUT_BYTES:
                        captured.write(s)
                        emitted_bytes += len(s.encode("utf-8", "replace"))
                    elif emitted_bytes == MAX_STDOUT_BYTES:
                        captured.write("\n…[stdout 截断，超过 256KB]")
                        emitted_bytes += 1
                return len(s)

            def flush(self) -> None:  # type: ignore[override]
                return None

        import contextlib

        try:
            with contextlib.redirect_stdout(_Tee()):
                exec(compiled, globals_dict)  # noqa: S102 — sandboxed by design
                if tail_compiled is not None:
                    result = eval(tail_compiled, globals_dict)  # noqa: S307
                    if result is not None:
                        result_repr = repr(result)[:MAX_REPR_CHARS]
        except SandboxViolationError as exc:
            _emit(
                "error",
                {
                    "etype": "SandboxViolationError",
                    "message": str(exc),
                    "traceback": traceback.format_exc()[:MAX_TRACEBACK_CHARS],
                },
            )
            return EXIT_SAFETY
        except BaseException as exc:  # noqa: BLE001 — report user errors verbatim
            _emit(
                "error",
                {
                    "etype": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc()[:MAX_TRACEBACK_CHARS],
                },
            )
            # User-level exception is still a *finished* cell: keep state.
            _finish(payload, globals_dict)
            return EXIT_DONE

        _finish(payload, globals_dict)
        _emit(
            "done",
            {
                "result_repr": result_repr,
                "stdout": captured.getvalue(),
                "charts": charts,
            },
        )
        return EXIT_DONE
    finally:
        uninstall_all()


def _finish(payload: pathlib.Path, globals_dict: dict) -> None:
    """Persist session state; a failed pickle save never fails the cell."""
    try:
        clean = _clean_globals(globals_dict)
        # Drop values that cannot survive pickling (e.g. injected closures,
        # locks) so one bad assignment does not poison the whole session.
        picklable: dict = {}
        for k, v in clean.items():
            try:
                pickle.dumps(v)
            except Exception:
                continue
            picklable[k] = v
        tmp = payload / "state.pkl.tmp"
        with tmp.open("wb") as fh:
            pickle.dump(picklable, fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(payload / "state.pkl")
    except Exception:  # pragma: no cover — best-effort persistence
        pass


if __name__ == "__main__":
    _rc = main()
    sys.stdout.flush()
    # The stdin-beacon daemon thread holds the stdin buffer lock — a normal
    # interpreter finalization would crash (Fatal Python error on Windows).
    # Same hard-exit as app.sandbox.runner.
    os._exit(_rc)
