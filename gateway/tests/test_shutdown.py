"""Shutdown-path tests — the clean-exit contract.

Regression cover for a bug where ``MT5Client.shutdown()`` latched
``_shutdown`` *before* hopping onto the worker queue, so ``run()`` rejected
the release job and ``shutdown()`` raised ``MT5TerminalUnavailable`` on every
call.  ``lifespan`` clears the run-state file immediately after this call, so
the exception skipped that too — leaving a state file behind that made the
*next* boot report ``abnormal_restart`` and fire a spurious
「请先核对持仓」critical alert on every ordinary restart.

Why the existing suite missed it: every other gateway test file's ``client``
fixture hand-stopped the worker (``c._shutdown = True; c._queue.put(None)``)
rather than calling ``shutdown()``.  Those fixtures now call the real method,
so the path is exercised by every gateway test; the explicit assertions live
here.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


FAKE = _NS(shutdown_calls=0, shutdown_raises=False)


def _make_mt5_module() -> types.ModuleType:
    m = types.ModuleType("MetaTrader5")
    m.__version__ = "5.0.0-fake"

    def initialize(**kw):
        return True

    def shutdown():
        FAKE.shutdown_calls += 1
        if FAKE.shutdown_raises:
            raise RuntimeError("terminal already gone")

    def last_error():
        return (1, "fake")

    def terminal_info():
        return _NS(connected=True)

    def symbols_total():
        return 1

    for fn in (initialize, shutdown, last_error, terminal_info, symbols_total):
        setattr(m, fn.__name__, fn)
    return m


sys.modules["MetaTrader5"] = _make_mt5_module()

from app import mt5_client as mc  # noqa: E402  (after the fake is installed)


@pytest.fixture
def client():
    sys.modules["MetaTrader5"] = _make_mt5_module()
    FAKE.shutdown_calls = 0
    FAKE.shutdown_raises = False
    c = mc.MT5Client()
    # Arm shutdown_sync's real release branch without a terminal: the init
    # path itself is covered by the other gateway test files.
    c._initialized = True
    yield c
    # Idempotent, so this is a no-op for tests that already shut down.
    asyncio.run(c.shutdown())


def _run(coro):
    return asyncio.run(coro)


# ── the regression ──────────────────────────────────────────────

def test_shutdown_does_not_raise(client):
    """Must never raise: the caller clears the run-state file right after."""
    _run(client.shutdown())  # raised MT5TerminalUnavailable before the fix


def test_shutdown_releases_the_terminal(client):
    _run(client.shutdown())
    assert FAKE.shutdown_calls == 1


def test_shutdown_stops_the_worker_thread(client):
    _run(client.shutdown())
    client._worker.join(timeout=2)
    assert not client._worker.is_alive()


def test_shutdown_is_idempotent(client):
    _run(client.shutdown())
    _run(client.shutdown())
    assert FAKE.shutdown_calls == 1


def test_shutdown_survives_a_failing_release(client):
    """A terminal that is already gone must not turn a clean exit into 'abnormal'."""
    FAKE.shutdown_raises = True
    _run(client.shutdown())
    client._worker.join(timeout=2)
    assert not client._worker.is_alive()


def test_run_rejects_new_work_after_shutdown(client):
    _run(client.shutdown())
    with pytest.raises(mc.MT5TerminalUnavailable):
        _run(client.run(lambda: 1))
