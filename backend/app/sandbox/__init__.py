"""Strategy sandbox runtime (backtest slice, M4).

Layout:
    payload.py       — task payload serialisation (json + bars.npz)
    import_hook.py   — whitelisted import finder + restricted builtins
    matching.py      — three-tier fill model + account/position simulator
    context.py       — BacktestContext: the runtime `ctx` object
    metrics.py       — pure report-metric functions
    engine.py        — BacktestEngine: bar-driven replay loop
    runner.py        — thin subprocess transport (`python -m app.sandbox.runner`)
"""

from __future__ import annotations

import sys
from pathlib import Path

# The strategy-facing SDK lives in <repo>/shared (outside the backend
# package). Sandbox modules import `sdk.*` at module level, so the path is
# registered here — before any submodule is imported.
_SHARED_DIR = Path(__file__).resolve().parents[3] / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))
