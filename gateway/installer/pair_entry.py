"""PyInstaller entry for the gateway pairing CLI (M17 配对码)."""

from __future__ import annotations

from app.pairing import main

if __name__ == "__main__":
    raise SystemExit(main())
