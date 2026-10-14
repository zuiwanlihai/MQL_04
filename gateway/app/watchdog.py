"""Gateway watchdog (M11, PRD M1 服务常驻).

Run INSTEAD of uvicorn directly::

    .venv\\Scripts\\python -m app.watchdog

It spawns the gateway as a child process and relaunches it whenever it exits
unexpectedly (crash / killed).  Clean exits (returncode 0, e.g. Ctrl+C after
the lifespan ran) are respected — no restart loop on a deliberate stop.

Give-up rule mirrors the terminal guard (PRD: 连续拉起失败 ≥3 次 → 停止重试等待
人工，避免无限抖动): 3 abnormal exits within ``RESTART_WINDOW`` → stop and log
 loudly so an operator / service manager notices.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [watchdog] %(levelname)s %(message)s")
logger = logging.getLogger("gateway.watchdog")

RESTART_COOLDOWN_SECONDS = 5
RESTART_WINDOW_SECONDS = 300
MAX_RESTARTS_IN_WINDOW = 3

GATEWAY_ROOT = Path(__file__).resolve().parents[1]


def _spawn() -> subprocess.Popen:
    """Start the gateway uvicorn child (single worker, no --reload)."""
    from app.config import settings

    cmd = [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", settings.GATEWAY_HOST, "--port", str(settings.GATEWAY_PORT),
    ]
    logger.info("spawning gateway: %s", " ".join(cmd))
    return subprocess.Popen(cmd, cwd=str(GATEWAY_ROOT))


def main() -> int:
    restarts: deque[float] = deque()
    while True:
        proc = _spawn()
        rc = proc.wait()
        now = time.time()

        if rc == 0:
            logger.info("gateway exited cleanly (rc=0) — watchdog stopping")
            return 0

        cutoff = now - RESTART_WINDOW_SECONDS
        while restarts and restarts[0] < cutoff:
            restarts.popleft()
        restarts.append(now)

        if len(restarts) > MAX_RESTARTS_IN_WINDOW:
            logger.critical(
                "gateway crashed %d times in %ds (last rc=%s) — giving up, "
                "manual intervention required (check MT5 terminal / logs)",
                len(restarts), RESTART_WINDOW_SECONDS, rc)
            return 1

        logger.warning("gateway exited abnormally (rc=%s); restarting in %ds "
                       "(%d/%d in window)", rc, RESTART_COOLDOWN_SECONDS,
                       len(restarts), MAX_RESTARTS_IN_WINDOW)
        time.sleep(RESTART_COOLDOWN_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
