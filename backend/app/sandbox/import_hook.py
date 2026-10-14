"""Sandbox import hook — the runtime enforcement point (M4, PRD §安全).

Three defensive layers, installed ONLY after the engine has finished all of
its own imports (``install_all`` is called by the runner right before the
strategy source is exec'd):

1. ``sys.meta_path`` whitelist finder — strategy code may only import the
   stdlib/third-party modules the static validator allows (single source of
   truth: ``app.services.strategy_validator`` tables) plus the ``sdk``
   package itself.  Anything else raises ``ILLEGAL_IMPORT``.
2. Restricted ``__builtins__`` — dangerous builtins (eval/exec/compile/
   ``__import__``/open/breakpoint) are removed from the strategy module
   globals; a controlled ``__import__`` re-checks the whitelist.
3. Escape-member runtime guards — ``datetime``/``random`` proxy views and
   numpy/pandas module-level ``__getattr__`` raise ``SANDBOX_VIOLATION``
   when a strategy touches a file/clock/random-seed entry point.  Static
   rule I004/F003 cannot catch ``getattr(np, "load")``; this layer can.

Violations surface as :class:`SandboxViolationError` carrying the SDK
``ErrorCode``; the engine turns them into a ``fail`` event.
"""

from __future__ import annotations

import builtins
import sys
from types import ModuleType
from typing import Optional

from sdk.errors import ErrorCode

from app.services.strategy_validator import (
    DANGEROUS_BUILTINS,
    ESCAPE_MEMBERS,
    STDLIB_WHITELIST,
    THIRD_PARTY_WHITELIST,
)

# Root-module prefixes always allowed.  `sdk` only — deliberately NOT the
# platform's own packages.  The finder gates on the *importer* being strategy
# code, so every `app.*` module the engine imported is already in sys.modules;
# a whitelisted `app.*` prefix therefore handed strategy code those live
# module objects, bypassing all three layers.  Concretely, `app.sandbox.payload`
# binds the real numpy at import time (before the layer-3 guard views exist),
# so `from app.sandbox.payload import np` yielded an unguarded `np.load` →
# arbitrary file read, and `app.sandbox.import_hook` binds the real `builtins`
# → `open`/`eval`.  Strategies reach the engine through `ctx`, never imports.
ALLOWED_PREFIXES: tuple[str, ...] = ("sdk",)

# Module name the engine exec's the strategy source under — the finder uses
# it to decide whether an import originates from strategy code.
STRATEGY_MODULE_NAME = "strategy_main"

# random module: instance methods must stay usable, but re-seeding would
# break determinism — the runner seeds once, strategies may not.
_RANDOM_GUARD_MEMBERS: frozenset[str] = frozenset({"seed"})


class SandboxViolationError(Exception):
    """Raised when strategy code touches a forbidden capability."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _root_of(name: str) -> str:
    return name.split(".")[0]


def is_module_allowed(name: str) -> bool:
    """Whitelist decision for a (possibly dotted) module name."""
    if name.startswith(ALLOWED_PREFIXES):
        return True
    root = _root_of(name)
    return root in STDLIB_WHITELIST or root in THIRD_PARTY_WHITELIST


def _importer_module_name() -> Optional[str]:
    """Walk up past importlib/builtins machinery frames to the module that
    triggered the import; None if it cannot be determined."""
    skip = ("importlib", "builtins", "app.sandbox.import_hook", "zipimport", "_distutils_hack")
    try:
        f = sys._getframe(1)
    except ValueError:
        return None
    while f is not None:
        name = f.f_globals.get("__name__", "")
        if not name or name.startswith(skip):
            f = f.f_back  # type: ignore[assignment]  # FrameType.f_back is Optional in stub
            continue
        return name
    return None


class WhitelistFinder:
    """``sys.meta_path`` entry rejecting non-whitelisted imports **made by
    strategy code**.  Transitive imports from already-whitelisted libraries
    (pandas → dateutil, statistics → fractions, …) are trusted: the gate is
    keyed on the immediate importer, not blanket-blocked, so legitimate
    strategy imports keep working while ``import os`` / ``import builtins``
    from strategy code raises even for already-loaded modules.
    """

    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001
        if is_module_allowed(fullname):
            return None
        importer = _importer_module_name()
        if importer != STRATEGY_MODULE_NAME:
            return None  # not strategy-originated — engine/lib internals
        raise ImportError(
            f"[sandbox] 模块 {fullname!r} 不在 SDK v1 白名单内 (ILLEGAL_IMPORT)"
        )


_finder: Optional[WhitelistFinder] = None


def install_import_finder() -> None:
    global _finder
    if _finder is None:
        _finder = WhitelistFinder()
        sys.meta_path.insert(0, _finder)


def uninstall_import_finder() -> None:
    global _finder
    if _finder is not None:
        try:
            sys.meta_path.remove(_finder)
        except ValueError:
            pass
        _finder = None


# ── Layer 2: restricted builtins ────────────────────────────────

def make_restricted_builtins() -> dict:
    """builtins dict with DANGEROUS_BUILTINS removed + controlled import.

    ``datetime`` is special-cased: strategy code receives a *scoped* guard
    view (clock members raise) while ``sys.modules['datetime']`` stays the
    real module — a global datetime replacement breaks pandas' C-level
    datetime init (infinite import recursion → stack overflow).
    """
    table = dict(vars(builtins))
    for name in DANGEROUS_BUILTINS:
        table.pop(name, None)

    def _safe_import(name: str, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001
        if level != 0:
            raise SandboxViolationError(
                ErrorCode.ILLEGAL_IMPORT, "沙箱禁止相对导入"
            )
        if not is_module_allowed(name):
            raise SandboxViolationError(
                ErrorCode.ILLEGAL_IMPORT,
                f"模块 {name!r} 不在 SDK v1 白名单内",
            )
        mod = builtins.__import__(name, globals, locals, fromlist, level)
        if _root_of(name) == "datetime":
            return _scoped_datetime_view()
        return mod

    table["__import__"] = _safe_import
    return table


def restricted_module_dict(module_name: str) -> dict:
    """Fresh module globals dict for exec'ing strategy source."""
    return {
        "__name__": module_name,
        "__file__": f"<sandbox:{module_name}>",
        "__builtins__": make_restricted_builtins(),
        "__doc__": None,
        "__spec__": None,
    }


# ── Layer 3: escape-member runtime guards ───────────────────────

_originals: dict[str, object] = {}


class _ModuleView(ModuleType):
    """Proxy module raising SandboxViolationError on guarded members.

    Non-guarded attributes are delegated lazily to the real module, so
    package lazy-attrs (numpy 2.x) keep working.
    """


def _make_guard_view(real: ModuleType, forbidden: frozenset[str]) -> _ModuleView:
    view = _ModuleView(real.__name__)
    view.__doc__ = real.__doc__
    view.__package__ = getattr(real, "__package__", "")
    view.__file__ = getattr(real, "__file__", None)
    real_path = getattr(real, "__path__", None)
    if real_path is not None:
        view.__path__ = real_path  # keep it a package view

    def _getattr(name: str):
        if name in forbidden:
            raise SandboxViolationError(
                ErrorCode.SANDBOX_VIOLATION,
                f"{real.__name__}.{name} 属于时钟/文件/随机种子逃逸入口，沙箱禁止",
            )
        return getattr(real, name)

    view.__getattr__ = _getattr  # type: ignore[method-assign]  # ModuleType base has __getattr__
    return view


def install_escape_guards() -> None:
    """Guard escape members on already-imported third-party modules.

    Only numpy/pandas/random get *global* proxy views (verified not to break
    their own import machinery).  ``datetime`` is deliberately NOT replaced
    in ``sys.modules`` — pandas' C extensions import the real datetime during
    their own init, and a global proxy makes their import recurse until the
    stack overflows.  Strategy-facing datetime guarding is scoped to the
    restricted ``__import__`` (see :func:`_scoped_datetime_view`).
    """
    _guard_package("random", _RANDOM_GUARD_MEMBERS)
    _guard_package("numpy", ESCAPE_MEMBERS["numpy"])
    _guard_package("pandas", ESCAPE_MEMBERS["pandas"])


_scoped_dt_view: Optional[_ModuleView] = None


def _scoped_datetime_view() -> _ModuleView:
    """Guarded datetime module handed to *strategy* imports only.

    Clock members (now/utcnow/today/fromtimestamp/strptime) raise
    SANDBOX_VIOLATION on both the module and the datetime/date/time classes;
    everything else delegates to the real module so isinstance/type checks
    and legitimate construction (``datetime.datetime(2024,1,1)``) work.
    """
    global _scoped_dt_view
    if _scoped_dt_view is not None:
        return _scoped_dt_view

    import datetime as _real

    forbidden = ESCAPE_MEMBERS["datetime"]

    def _blocked(*a, **k):
        raise SandboxViolationError(
            ErrorCode.SANDBOX_VIOLATION,
            "datetime 时钟方法属于逃逸入口，沙箱禁止；时间一律取自 ctx",
        )

    def _guarded(cls):
        class _GuardMeta(type(cls)):
            def __instancecheck__(mcs, inst):
                return isinstance(inst, cls)

            def __subclasscheck__(mcs, sub):
                return issubclass(sub, cls)

        return _GuardMeta(
            cls.__name__, (cls,),
            {"_sandbox_guarded": True,
             **{m: classmethod(_blocked) for m in forbidden if hasattr(cls, m)}},
        )

    view = _ModuleView("datetime")
    view.__doc__ = _real.__doc__
    view.__file__ = _real.__file__
    view.date = _guarded(_real.date)  # type: ignore[attr-defined]  # dynamic on _ModuleView
    view.time = _guarded(_real.time)  # type: ignore[attr-defined]
    view.datetime = _guarded(_real.datetime)  # type: ignore[attr-defined]
    view.timedelta = _real.timedelta  # type: ignore[attr-defined]
    view.timezone = _real.timezone  # type: ignore[attr-defined]
    view.tzinfo = _real.tzinfo  # type: ignore[attr-defined]
    view.MINYEAR = _real.MINYEAR  # type: ignore[attr-defined]
    view.MAXYEAR = _real.MAXYEAR  # type: ignore[attr-defined]

    _extra = set(dir(_real)) - set(vars(view)) - forbidden
    for attr in _extra:
        setattr(view, attr, getattr(_real, attr))

    def _getattr(name: str):
        if name in forbidden:
            raise SandboxViolationError(
                ErrorCode.SANDBOX_VIOLATION,
                f"datetime.{name} 属于时钟逃逸入口，沙箱禁止",
            )
        return getattr(_real, name)

    view.__getattr__ = _getattr  # type: ignore[method-assign]  # ModuleType base has __getattr__
    _scoped_dt_view = view
    return view


def _guard_package(name: str, forbidden: frozenset[str]) -> None:
    real = sys.modules.get(name)
    if real is None or isinstance(real, _ModuleView):
        return
    _originals[name] = real
    sys.modules[name] = _make_guard_view(real, forbidden)


# ── Orchestration ───────────────────────────────────────────────

def install_all() -> None:
    """Install all three layers. Call AFTER the engine finished importing."""
    install_import_finder()
    install_escape_guards()


def uninstall_all() -> None:
    """Remove the finder and restore every patched sys.modules entry."""
    uninstall_import_finder()
    for name, original in _originals.items():
        sys.modules[name] = original  # type: ignore[assignment]  # _originals.values() is object
    _originals.clear()
