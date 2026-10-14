"""Import-hook unit tests (no subprocess)."""

from __future__ import annotations

import sys

import pytest

from app.sandbox.import_hook import (
    SandboxViolationError,
    WhitelistFinder,
    install_all,
    is_module_allowed,
    make_restricted_builtins,
    restricted_module_dict,
    uninstall_all,
)


@pytest.fixture(autouse=True)
def _clean():
    yield
    uninstall_all()


# ── whitelist decision ─────────────────────────────────────────

def test_allowed_stdlib_and_third_party():
    assert is_module_allowed("math")
    assert is_module_allowed("statistics")
    assert is_module_allowed("numpy")
    assert is_module_allowed("pandas.io")  # dotted root checked
    assert is_module_allowed("scipy.stats")


def test_forbidden_modules_rejected():
    for name in ("os", "sys", "socket", "subprocess", "requests", "time", "pathlib"):
        assert not is_module_allowed(name)


def test_platform_prefixes_allowed():
    assert is_module_allowed("sdk.errors")
    # Platform internals are NOT a strategy-facing entry point.  Allowing the
    # ``app.sandbox`` prefix let strategy code import the sandbox's own
    # plumbing (see test below); the SDK is the only platform surface a
    # strategy needs, and it reaches the engine through ``ctx``, not imports.
    assert not is_module_allowed("app.sandbox.engine")
    assert not is_module_allowed("app.services.strategy_validator")


# ── restricted builtins ────────────────────────────────────────

def test_dangerous_builtins_removed():
    table = make_restricted_builtins()
    for name in ("eval", "exec", "compile", "open", "breakpoint"):
        assert name not in table
    assert callable(table["__import__"])


def test_restricted_module_dict_shape():
    d = restricted_module_dict("strategy_main")
    assert d["__name__"] == "strategy_main"
    assert "eval" not in d["__builtins__"]


def test_safe_import_blocks_os():
    imp = make_restricted_builtins()["__import__"]
    with pytest.raises(SandboxViolationError):
        imp("os")


def test_safe_import_relative_rejected():
    imp = make_restricted_builtins()["__import__"]
    with pytest.raises(SandboxViolationError):
        imp("math", level=1)


def test_safe_import_datetime_returns_guarded_view():
    imp = make_restricted_builtins()["__import__"]
    dt = imp("datetime")
    import datetime as real

    assert sys.modules["datetime"] is real  # global untouched
    with pytest.raises(SandboxViolationError):
        dt.datetime.now()


def test_safe_import_math_ok():
    imp = make_restricted_builtins()["__import__"]
    m = imp("math")
    assert m.sqrt(4) == 2


# ── finder origin gating ───────────────────────────────────────

def test_finder_allows_whitelisted():
    assert WhitelistFinder().find_spec("math", None, None) is None


def test_exec_under_restricted_dict_import_os_fails():
    install_all()
    d = restricted_module_dict("strategy_main")
    with pytest.raises(Exception) as ei:
        exec("import os", d)
    assert "os" in str(ei.value)


def test_exec_under_restricted_dict_import_math_ok():
    install_all()
    d = restricted_module_dict("strategy_main")
    exec("import math", d)
    assert d["math"].sqrt(9) == 3


def test_numpy_guard_view_blocks_load():
    import numpy

    assert isinstance(numpy.load, type(None)) or callable(numpy.load)  # pre-state
    install_all()
    import numpy as np2

    with pytest.raises(SandboxViolationError):
        np2.load  # noqa: B018 — attribute access must raise
    # legitimate use still works
    assert np2.arange(3).sum() == 3


def test_uninstall_restores_numpy():
    install_all()
    uninstall_all()
    import numpy

    assert callable(numpy.load)


# ── escape regression: platform internals are not strategy-reachable ───

@pytest.mark.parametrize(
    "src",
    [
        # payload.py binds the REAL numpy at import time (line 20) — before the
        # layer-3 guard views replace sys.modules["numpy"].  Reaching it hands
        # strategy code np.load → arbitrary file read.
        "from app.sandbox.payload import np",
        "import app.sandbox.payload",
        # import_hook.py imports `builtins` at module level (line 25), so this
        # returns the real builtins module → open / eval / __import__.
        "from app.sandbox.import_hook import builtins",
        "from app.services.strategy_validator import builtins",
    ],
)
def test_strategy_cannot_reach_platform_internals(src):
    """``app.*`` must not be importable from strategy code.

    The finder gates on the *importer* being ``strategy_main``, so every
    ``app.*`` module the engine itself imported is already in ``sys.modules``
    — the whitelist prefix was the only thing standing between strategy code
    and those live module objects.
    """
    install_all()
    d = restricted_module_dict("strategy_main")
    with pytest.raises(SandboxViolationError):
        exec(src, d)  # noqa: S102 — the sandbox is the thing under test
