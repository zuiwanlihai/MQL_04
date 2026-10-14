"""Subprocess supervisor for sandbox runner tasks (M4 Phase D).

Spawns ``python -m app.sandbox.runner --payload <dir>`` under asyncio
subprocess transport and streams its JSONL stdout back as parsed event
dicts.  The parent is responsible for the three resource caps:

* RSS polling every 2s (psutil) → kill + RESOURCE_EXCEEDED
* total wall-clock cap (BACKTEST_TIMEOUT_SECONDS) → kill + timed_out
* user cancel → recursive process-tree kill (psutil children)

Windows pipeline discipline (verified in the Phase C smoke tests):
``stdin`` stays an OPEN pipe — the runner's EOF beacon would otherwise fire
immediately and the child would exit 143; ``stderr`` is redirected to a
file so a chatty strategy can never deadlock the 64KB stdout pipe.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional, cast

import psutil

from app.config import PROJECT_ROOT, settings
from app.sandbox.runner_io import HEARTBEAT_EVENT

logger = logging.getLogger("mt5quant.backtest.supervisor")

EXIT_DONE = 0
EXIT_SAFETY = 2
EXIT_LOAD_FAILED = 3
EXIT_PARENT_GONE = 143

# Termination outcomes (distinct from runner exit codes).
TERM_COMPLETED = "completed"
TERM_FAILED = "failed"
TERM_CANCELLED = "cancelled"
TERM_TIMEOUT = "timeout"
TERM_RESOURCE = "resource"
TERM_CRASH = "crash"
# Child stopped producing output — not even heartbeats — while it should be
# alive.  Only reachable when ``stall_timeout`` is set (long-lived paper/live
# runs, which pass ``timeout=None`` and would otherwise never be killed).
TERM_STALLED = "stalled"

OnEvent = Callable[[dict], Awaitable[None]]

# Sentinel: ``run()`` applies BACKTEST_TIMEOUT_SECONDS unless the caller passes
# an explicit value (paper runs pass ``timeout=None`` = no cap).
_DEFAULT = object()

# Max bytes for a single JSONL stdout line. The runner truncates each event to
# 262_144 chars; with ensure_ascii=False a Chinese-heavy line can be up to ~4
# bytes/char, and a 1500-point equity curve serialises to well over the 64 KB
# asyncio stream default. The read pipe limit must be raised above that ceiling
# or readline() raises LimitOverrunError ("Separator is found, but chunk is
# longer than limit") and crashes the whole task.
STREAM_LIMIT = 1_100_000


@dataclass
class SuperviseResult:
    outcome: str  # TERM_* constant
    exit_code: Optional[int] = None
    timed_out: bool = False
    stderr_tail: str = ""
    events: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome == TERM_COMPLETED


class RunnerSupervisor:
    """One runner subprocess + its watchdog tasks for a single task row.

    ``module`` selects the subprocess entry-point: the M4 batch
    ``app.sandbox.runner`` (default) or the M5 long-lived
    ``app.sandbox.paper_runner``.  For paper runs pass ``timeout=None`` —
    the process must not be killed by the backtest wall-clock cap; control
    commands go to its stdin via :meth:`send`.

    Because ``timeout=None`` also removes the *only* kill switch, long-lived
    runs pass ``stall_timeout`` as well: the child heartbeats on stdout from a
    thread that does not run strategy code (see
    :mod:`app.sandbox.runner_io`), so "nothing at all for N seconds" is an
    unambiguous hang rather than a quiet market.
    """

    def __init__(
        self,
        task_id: str,
        payload_dir: Path,
        module: str = "app.sandbox.runner",
        stall_timeout: Optional[float] = None,
    ) -> None:
        self.task_id = task_id
        self.payload_dir = payload_dir
        self.module = module
        self.stall_timeout = stall_timeout
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._killed_reason: Optional[str] = None
        self._cancel_requested = False

    def _poll_interval(self) -> float:
        """读管道时的等待间隔。

        默认 5s 足够；一旦启用卡死判定，检测精度就受这个间隔限制，所以按
        ``stall_timeout`` 的尺度缩短——否则设 30s 阈值却要等 35s 才发现。
        """
        if self.stall_timeout is None:
            return 5.0
        return max(0.25, min(5.0, self.stall_timeout / 4))

    def _stalled(self, idle_seconds: float) -> bool:
        if self.stall_timeout is None or idle_seconds <= self.stall_timeout:
            return False
        logger.warning(
            "task %s 连续 %.1fs 无任何输出（含心跳，阈值 %.0fs）— 判定卡死，杀进程树",
            self.task_id, idle_seconds, self.stall_timeout,
        )
        return True

    async def send(self, cmd: dict) -> bool:
        """Write one JSONL control command to the child's stdin and flush.

        Returns False when the process is gone (caller finalises)."""
        proc = self._proc
        if proc is None or proc.returncode is not None or proc.stdin is None:
            return False
        try:
            proc.stdin.write(
                (json.dumps(cmd, ensure_ascii=False, default=str) + "\n").encode("utf-8")
            )
            await proc.stdin.drain()
        except (ConnectionResetError, BrokenPipeError, OSError, ValueError):
            return False
        return True

    # -- kill helpers ---------------------------------------------------------
    def _kill_tree(self) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        pid = self._proc.pid
        try:
            parent = psutil.Process(pid)
            victims = parent.children(recursive=True) + [parent]
        except psutil.NoSuchProcess:
            victims = []
        for p in victims:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass

    def request_cancel(self) -> None:
        self._cancel_requested = True
        self._killed_reason = self._killed_reason or TERM_CANCELLED
        self._kill_tree()

    async def _rss_watch(self, interval: float = 2.0) -> None:
        limit_bytes = settings.SANDBOX_MAX_MEMORY_MB * 1024 * 1024
        proc = self._proc
        assert proc is not None
        while proc.returncode is None:
            await asyncio.sleep(interval)
            if proc.returncode is not None:
                return
            try:
                p = psutil.Process(proc.pid)
                rss = p.memory_info().rss + sum(
                    c.memory_info().rss for c in p.children(recursive=True)
                )
            except psutil.NoSuchProcess:
                return
            if rss > limit_bytes:
                logger.warning(
                    "task %s RSS %.1fMB > %.1fMB — killing",
                    self.task_id, rss / 2**20, settings.SANDBOX_MAX_MEMORY_MB,
                )
                self._killed_reason = TERM_RESOURCE
                self._kill_tree()
                return

    # -- main streaming loop ---------------------------------------------------
    async def run(
        self, on_event: OnEvent, timeout: object = _DEFAULT
    ) -> SuperviseResult:
        """Stream the child's JSONL stdout until EOF.

        ``timeout`` is the total wall-clock cap; the default applies
        BACKTEST_TIMEOUT_SECONDS.  Paper runs pass ``None`` — the process
        lives until the parent stops it or it fails on its own.
        """
        if timeout is _DEFAULT:
            timeout = float(settings.BACKTEST_TIMEOUT_SECONDS)
        # sentinel 检查后 timeout 只能是 float 或 None；cast 绕过 object 类型声明
        _timeout: float | None = cast(float | None, timeout)
        stderr_path = self.payload_dir / "runner_stderr.log"
        stderr_file = open(stderr_path, "wb")  # noqa: SIM115 — closed in finally
        env = {
            **os.environ,
            "PYTHONHASHSEED": "0",
            "PYTHONUNBUFFERED": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
        }
        try:
            self._proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", self.module,
                "--payload", str(self.payload_dir),
                cwd=str(PROJECT_ROOT),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr_file,
                env=env,
                limit=STREAM_LIMIT,
            )
        except OSError as exc:
            stderr_file.close()
            return SuperviseResult(
                outcome=TERM_CRASH, exit_code=None, stderr_tail=str(exc)[:4000]
            )

        result = SuperviseResult(outcome=TERM_COMPLETED)
        rss_task = asyncio.create_task(self._rss_watch())
        deadline = (
            None if _timeout is None
            else asyncio.get_running_loop().time() + _timeout
        )
        try:
            assert self._proc.stdout is not None
            loop = asyncio.get_running_loop()
            last_line_at = loop.time()
            while True:
                wait_for = self._poll_interval()
                if deadline is not None:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        self._killed_reason = TERM_TIMEOUT
                        self._kill_tree()
                        break
                    wait_for = min(wait_for, remaining)
                try:
                    line = await asyncio.wait_for(
                        self._proc.stdout.readline(), timeout=wait_for
                    )
                except asyncio.TimeoutError:
                    if self._cancel_requested:
                        self._killed_reason = TERM_CANCELLED
                        self._kill_tree()
                        break
                    if self._stalled(loop.time() - last_line_at):
                        self._killed_reason = TERM_STALLED
                        self._kill_tree()
                        break
                    continue
                if not line:
                    break  # stdout EOF — child is finishing
                last_line_at = loop.time()
                if len(line) > STREAM_LIMIT - 1024:
                    logger.warning("task %s: oversized event line dropped", self.task_id)
                    continue
                try:
                    event = json.loads(line.decode("utf-8", errors="replace"))
                except ValueError:
                    logger.warning(
                        "task %s: non-JSON stdout line: %.200r", self.task_id, line
                    )
                    continue
                if event.get("event") == HEARTBEAT_EVENT:
                    continue  # 传输层保活，不是引擎事件
                result.events.append(event)
                await on_event(event)
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                self._kill_tree()
                await self._proc.wait()
        finally:
            rss_task.cancel()
            with suppress(asyncio.CancelledError):
                await rss_task
            stderr_file.close()

        result.exit_code = self._proc.returncode
        result.stderr_tail = _tail(stderr_path, 4000)
        result.outcome = self._classify(result.exit_code)
        result.timed_out = result.outcome == TERM_TIMEOUT
        return result

    def _classify(self, exit_code: Optional[int]) -> str:
        reason = self._killed_reason
        if reason == TERM_CANCELLED or self._cancel_requested:
            return TERM_CANCELLED
        if reason == TERM_RESOURCE:
            return TERM_RESOURCE
        if reason == TERM_TIMEOUT:
            return TERM_TIMEOUT
        if reason == TERM_STALLED:
            return TERM_STALLED
        if exit_code == EXIT_DONE:
            return TERM_COMPLETED
        if exit_code in (EXIT_SAFETY, EXIT_LOAD_FAILED):
            return TERM_FAILED
        return TERM_CRASH  # 143 (orphan beacon) / negative signals / nonzero


def _tail(path: Path, max_chars: int) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-max_chars:].decode("utf-8", errors="replace")
