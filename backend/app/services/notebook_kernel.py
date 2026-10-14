"""Research notebook kernel service (M18 研究笔记本).

One cell = one supervised subprocess (``app.sandbox.notebook_runner``) via
the shared :class:`RunnerSupervisor`.  Session state (cell globals) lives in
a pickle file inside the per-notebook payload directory; the file is
rewritten by the child at the end of every finished cell.

Data flow: the sandbox forbids sockets, so this service AST-scans the cell
for **literal** ``get_bars(...)`` calls, pre-fetches every dataset from the
gateway, and drops the rows into ``bars/{key}.json`` — the injected
``get_bars`` serves them locally inside the child.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.services.backtest.supervisor import (
    TERM_COMPLETED,
    RunnerSupervisor,
    SuperviseResult,
)
from app.services.gateway_client import GatewayClient

logger = logging.getLogger("mt5quant.notebook.kernel")

# Notebooks are bounded data-research cells — no multi-minute backtests here.
RUNNER_MODULE = "app.sandbox.notebook_runner"

MAX_BARS_PAYLOAD_BYTES = 8 * 1024 * 1024  # one pre-fetched dataset cap


@dataclass
class CellOutcome:
    """Result of one cell execution (already JSON-ready shapes)."""

    ok: bool
    stdout: str = ""
    result_repr: str | None = None
    charts: list = field(default_factory=list)
    etype: str | None = None
    message: str | None = None
    traceback: str | None = None
    terminated: str | None = None  # timeout / resource / crash …
    state_reset: bool = False


@dataclass
class _Session:
    lock: asyncio.Lock
    last_used: float


_sessions: dict[uuid.UUID, _Session] = {}
_global_gate: asyncio.Semaphore | None = None


def _gate() -> asyncio.Semaphore:
    global _global_gate
    if _global_gate is None:
        _global_gate = asyncio.Semaphore(settings.NOTEBOOK_MAX_CONCURRENCY)
    return _global_gate


def _runtime_dir(notebook_id: uuid.UUID) -> Path:
    return Path(settings.BACKTEST_RUNTIME_DIR) / f"nb_{notebook_id}"


def reset_session(notebook_id: uuid.UUID) -> None:
    """Drop the pickled globals — the next cell starts a fresh namespace."""
    shutil.rmtree(_runtime_dir(notebook_id), ignore_errors=True)
    _sessions.pop(notebook_id, None)


def sweep_idle_sessions() -> int:
    """Remove state of sessions idle beyond the TTL; returns count swept."""
    ttl = settings.NOTEBOOK_SESSION_TTL_MINUTES * 60
    now = time.monotonic()
    swept = 0
    for nb_id, sess in list(_sessions.items()):
        if now - sess.last_used > ttl:
            shutil.rmtree(_runtime_dir(nb_id), ignore_errors=True)
            _sessions.pop(nb_id, None)
            swept += 1
    return swept


_SWEEP_INTERVAL_SECONDS = 300  # check cadence; TTL itself governs eviction


async def sweep_loop() -> None:
    """Lifespan loop: periodically evict idle notebook sessions."""
    while True:
        await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
        try:
            swept = sweep_idle_sessions()
            if swept:
                logger.info("notebook sweep evicted %d idle session(s)", swept)
        except Exception:  # noqa: BLE001
            logger.exception("notebook sweep loop iteration failed")


def _bars_key(symbol: str, timeframe: str, count: int) -> str:
    return f"{symbol.upper()}_{timeframe.upper()}_{count}"


def _literal_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_int(node: ast.AST) -> int | None:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    ):
        return node.value
    return None


def extract_bars_requests(source: str) -> list[tuple[str, str, int]]:
    """Collect literal ``get_bars(symbol, timeframe, count)`` calls (deduped)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found: dict[str, tuple[str, str, int]] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "get_bars"
        ):
            continue
        args = node.args
        symbol = _literal_str(args[0]) if len(args) >= 1 else None
        if symbol is None:
            continue  # non-literal → runtime error with a guiding message
        timeframe = "H1"
        if len(args) >= 2:
            timeframe = _literal_str(args[1]) or "H1"
        count = 500
        if len(args) >= 3:
            count = _literal_int(args[2]) or 500
        for kw in node.keywords:
            if kw.arg == "timeframe":
                timeframe = _literal_str(kw.value) or timeframe
            elif kw.arg == "count":
                count = _literal_int(kw.value) or count
        count = max(1, min(count, settings.NOTEBOOK_MAX_BARS_PER_CALL))
        symbol, timeframe = symbol.upper(), timeframe.upper()
        key = _bars_key(symbol, timeframe, count)
        found[key] = (symbol, timeframe, count)
    return list(found.values())


async def _prefetch_bars(requests: list[tuple[str, str, int]], bars_dir: Path) -> str | None:
    """Fetch every literal dataset; returns an error message on failure."""
    if not requests:
        return None
    bars_dir.mkdir(parents=True, exist_ok=True)
    client = GatewayClient()
    try:
        for symbol, timeframe, count in requests:
            key = _bars_key(symbol, timeframe, count)
            rows = await client.rates(symbol.upper(), timeframe.upper(), count)
            payload = json.dumps(rows, ensure_ascii=False, default=str)
            if len(payload.encode("utf-8")) > MAX_BARS_PAYLOAD_BYTES:
                return f"get_bars({symbol!r}, {timeframe!r}, {count!r}) 数据超过 8MB，请减小 count"
            (bars_dir / f"{key}.json").write_text(payload, encoding="utf-8")
    finally:
        await client.aclose()
    return None


def _state_or_none(payload_dir: Path) -> bool:
    return (payload_dir / "state.pkl").exists()


async def run_cell(notebook_id: uuid.UUID, source: str) -> CellOutcome:
    """Execute one notebook cell in a supervised subprocess."""
    session = _sessions.setdefault(
        notebook_id, _Session(lock=asyncio.Lock(), last_used=time.monotonic())
    )
    async with session.lock:
        return await _run_cell_locked(notebook_id, source, session)


async def _run_cell_locked(notebook_id: uuid.UUID, source: str, session: _Session) -> CellOutcome:
    session.last_used = time.monotonic()
    payload_dir = _runtime_dir(notebook_id)
    had_state = _state_or_none(payload_dir)
    payload_dir.mkdir(parents=True, exist_ok=True)

    # 1. Pre-fetch literal get_bars datasets (fresh cells may request more).
    bars_dir = payload_dir / "bars"
    error = await _prefetch_bars(extract_bars_requests(source), bars_dir)
    if error:
        return CellOutcome(
            ok=False, etype="PrefetchError", message=error, state_reset=not had_state
        )

    # 2. Write the cell.
    (payload_dir / "cell.py").write_text(source, encoding="utf-8")
    meta = {"bars": sorted(p.stem for p in bars_dir.glob("*.json"))}
    (payload_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    # 3. Supervise one runner process.
    supervisor = RunnerSupervisor(f"nb_{notebook_id}", payload_dir, module=RUNNER_MODULE)
    result: SuperviseResult = await supervisor.run(
        _noop, timeout=float(settings.NOTEBOOK_CELL_TIMEOUT_SECONDS)
    )
    session.last_used = time.monotonic()

    if result.outcome != TERM_COMPLETED:
        # Timeout / OOM / crash leaves the pickle of unknown quality → reset.
        reset_session(notebook_id)
        # The child usually emits a final error event (sandbox violation,
        # syntax error) before exiting non-zero — surface it if present.
        err = None
        for evt in result.events:
            if evt.get("event") == "error":
                err = evt
        message = {
            "timeout": (
                f"cell 执行超时（>{settings.NOTEBOOK_CELL_TIMEOUT_SECONDS}s），"
                "会话状态已重置"
            ),
            "resource": "cell 内存超限，会话状态已重置",
            "crash": "cell 子进程崩溃，会话状态已重置",
            "cancelled": "cell 被取消，会话状态已重置",
        }.get(result.outcome, f"cell 异常终止（{result.outcome}），会话状态已重置")
        return CellOutcome(
            ok=False,
            etype=err.get("etype") if err else None,
            message=err.get("message") if err else message,
            traceback=(err.get("traceback") if err else None)
            or (result.stderr_tail[:4000] or None),
            terminated=result.outcome,
            state_reset=True,
        )

    # 4. Parse the child's final event.
    done_evt = None
    error_evt = None
    for evt in result.events:
        kind = evt.get("event")
        if kind == "done":
            done_evt = evt
        elif kind == "error":
            error_evt = evt

    if error_evt is not None and done_evt is None:
        return CellOutcome(
            ok=False,
            etype=error_evt.get("etype"),
            message=error_evt.get("message"),
            traceback=error_evt.get("traceback"),
        )

    if done_evt is None:  # child exited 0 without a terminal event — treat as crash
        reset_session(notebook_id)
        return CellOutcome(
            ok=False,
            terminated="crash",
            message="cell 子进程无结果返回，会话状态已重置",
            state_reset=True,
        )

    return CellOutcome(
        ok=True,
        stdout=done_evt.get("stdout", ""),
        result_repr=done_evt.get("result_repr"),
        charts=done_evt.get("charts", []),
    )


async def _noop(_evt: dict) -> None:  # pragma: no cover — event sink
    return None


def session_iso_now() -> str:
    return datetime.now(UTC).isoformat()
