"""Put <repo>/shared on sys.path so tests can ``import sdk`` (same shim as backend)."""

import sys
from pathlib import Path

_SHARED_DIR = str(Path(__file__).resolve().parents[1])
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)
