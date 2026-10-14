"""Shared base class for paper and live run managers.

P2-6 extraction (2026-10-14): 3 methods are byte-for-byte identical between
``services/paper/manager.py`` and ``services/live/manager.py`` — they live here
now.  Each subclass keeps all trading/DB/model-specific code in its own module.

Extracted (zero-diff) methods:
  - ``__init__``      handles dict + GC-guard task set
  - ``spawn_start``   fire-and-forget start() with strong task ref
  - ``active_count``  returns len(_handles)

Concrete subclasses MUST override (not extracted — model/DB/behavior differ):
  - ``start``          orchestration differs wildly (paper = sandbox subprocess,
                       live = real gateway RPC risk-gated)
  - ``startup_sweep``  SQLAlchemy model differs (PaperRun vs LiveRun)
  - ``shutdown``       log message differs ("paper" vs "live")
  - ... all other trading methods

Generic type parameter ``HandleT`` lets mypy infer the concrete ``_RunHandle``
from each subclass without duplicating the ``__init__`` body.

See also: ``services/paper/manager.py``, ``services/live/manager.py``.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

HandleT = TypeVar("HandleT")


class BaseRunManager(ABC, Generic[HandleT]):
    """Infrastructure-only base.  Subclassed by PaperManager / LiveManager."""

    def __init__(self) -> None:
        self._handles: dict[str, HandleT] = {}
        self._start_tasks: set[asyncio.Task] = set()

    def spawn_start(self, run_id: str) -> None:
        """Fire-and-forget start with a strong task reference (GC guard)."""
        task = asyncio.get_running_loop().create_task(self.start(run_id))
        self._start_tasks.add(task)
        task.add_done_callback(self._start_tasks.discard)

    @abstractmethod
    async def start(self, run_id: str) -> None:  # pragma: no cover — interface
        """Concrete orchestration (differs wildly between paper/live)."""

    @abstractmethod
    async def startup_sweep(self) -> None:  # pragma: no cover — interface
        """Mark rows orphaned by backend restart as failed (model differs)."""

    def active_count(self) -> int:
        return len(self._handles)
