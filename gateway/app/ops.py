"""Gateway self-healing ops (M11, PRD M1 终端守护 + 异常重启提示).

Two responsibilities:

1. **Abnormal-restart flag** — a state file is written on startup and deleted
   on clean shutdown.  If the file exists at boot, the previous run did NOT
   exit cleanly (crash / kill / power loss) → the ``/health`` payload carries
   ``abnormal_restart=true`` so the backend raises the「请先核对持仓」alert
   (PRD: 崩溃期间交易状态可能已变化，不得静默恢复).

2. **Terminal guard** — a background loop probes ``terminal_info()``; when the
   MT5 terminal is unreachable it relaunches the terminal process, capped at
   ``TERMINAL_GUARD_MAX_RESTARTS`` within a rolling window (PRD: 连续失败 ≥3 次
   → 停止重试等待人工，避免无限抖动).  The next MT5 call self-heals via the
   existing ``_ensure_initialized_sync`` retry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger("gateway.ops")

STATE_FILE = Path(settings.RUNTIME_DIR_ROOT) / "gateway_state.json"

# Set by the lifespan when a stale state file proves the previous run crashed.
# Read by /health so the backend heartbeat can raise the「核对持仓」alert.
ABNORMAL_RESTART: dict[str, Any] | None = None

# Mutable one-element list: the guard loop flips it when it gives up after the
# restart cap; /health exposes it as ``guard_gave_up`` for the backend alert.
GIVEUP_FLAG = [False]


# ── abnormal-restart state file ─────────────────────────────────────────────

def write_state_file() -> dict[str, Any]:
    """Persist current run marker; returns the PREVIOUS state (if any)."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    previous: dict[str, Any] | None = None
    if STATE_FILE.exists():
        try:
            previous = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — corrupt file still counts as abnormal
            previous = {"corrupt": True}
    now = time.time()
    STATE_FILE.write_text(json.dumps({
        "pid": os.getpid(),
        "started_at": now,
        "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }), encoding="utf-8")
    return previous


def clear_state_file() -> None:
    try:
        STATE_FILE.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        logger.warning("failed to clear gateway state file")


# ── terminal guard loop ─────────────────────────────────────────────────────

class TerminalGuard:
    def __init__(self, client: Any) -> None:
        self._client = client
        self._restarts: deque[float] = deque()
        self._gave_up = False

    def _within_window(self) -> bool:
        cutoff = time.time() - settings.TERMINAL_GUARD_WINDOW_SECONDS
        while self._restarts and self._restarts[0] < cutoff:
            self._restarts.popleft()
        return len(self._restarts) < settings.TERMINAL_GUARD_MAX_RESTARTS

    def _launch_terminal_sync(self) -> bool:
        try:
            subprocess.Popen([settings.MT5_PATH], close_fds=True)  # noqa: S603
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("terminal launch failed: %s", exc)
            return False

    async def probe_and_heal(self) -> dict[str, Any]:
        """One guard tick.  Returns {reachable, launched, gave_up, restarts}."""
        reachable = await self._client.run(lambda: (
            self._client._mt5 is not None  # noqa: SLF001
            and self._client._mt5.terminal_info() is not None  # noqa: SLF001
        ))
        if reachable:
            return {"reachable": True, "launched": False,
                    "gave_up": self._gave_up, "restarts": len(self._restarts)}

        if self._gave_up:
            return {"reachable": False, "launched": False,
                    "gave_up": True, "restarts": len(self._restarts)}

        if not self._within_window():
            self._gave_up = True
            GIVEUP_FLAG[0] = True
            logger.error("terminal guard: exceeded %d restarts in %ds window — "
                         "stop retrying, manual intervention required",
                         settings.TERMINAL_GUARD_MAX_RESTARTS,
                         settings.TERMINAL_GUARD_WINDOW_SECONDS)
            return {"reachable": False, "launched": False,
                    "gave_up": True, "restarts": len(self._restarts)}

        launched = await asyncio.to_thread(self._launch_terminal_sync)
        if launched:
            self._restarts.append(time.time())
        logger.warning("terminal unreachable → launch %s (restart #%d)",
                       "ok" if launched else "failed", len(self._restarts))
        return {"reachable": False, "launched": launched,
                "gave_up": False, "restarts": len(self._restarts)}

    def reset_giveup(self) -> None:
        self._gave_up = False


async def guard_loop(guard: TerminalGuard) -> None:
    """Forever loop started from lifespan — never dies on a probe error."""
    while True:
        try:
            await guard.probe_and_heal()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("terminal guard iteration failed")
        await asyncio.sleep(settings.TERMINAL_GUARD_INTERVAL_SECONDS)
