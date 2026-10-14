"""PyInstaller entry for the packaged MT5 gateway service (M17 安装包).

The watchdog spawns ``python -m uvicorn`` children, which does not work
inside a frozen exe (``sys.executable`` is the exe itself), so the packaged
build starts uvicorn programmatically and relies on the Windows service
(NSSM) for keep-alive instead.  Configuration comes from ``.env`` next to
the exe (GATEWAY_HOST / GATEWAY_PORT / GATEWAY_API_KEY / MT5_*).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller onefile/onedir
        return Path(sys.executable).parent
    return Path(__file__).parent


BASE_DIR = _base_dir()
os.chdir(BASE_DIR)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("GATEWAY_HOST", "127.0.0.1"),
        port=int(os.getenv("GATEWAY_PORT", "8090")),
        workers=1,
        log_level="info",
    )


if __name__ == "__main__":
    main()
