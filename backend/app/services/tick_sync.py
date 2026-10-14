"""Serial tick-sync executor (M-Tick, PRD 数据中心 P1).

One singleton :class:`TickSyncQueue` owns a single worker consuming job ids
from an :class:`asyncio.Queue`.  Per job:

    claim (pending→running) → slice [start, end) into TICK_SYNC_CHUNK_SECONDS
    windows → per window ``gateway.ticks_range`` → batch
    ``INSERT … ON CONFLICT DO NOTHING`` (composite (symbol, time_msc) key
    makes re-sync idempotent) → tick_count bookkeeping → done/failed.

Startup sweep marks orphaned ``running`` rows failed (previous process died
mid-job).  A job whose gateway slice returns exactly the fetch limit may be
truncated — the row count is still recorded and the coverage query stays
truthful; re-running an overlapping window fills gaps (conflict-do-nothing).
"""

from __future__ import annotations

import asyncio
import logging
import uuid as uuid_mod
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.engine.session import AsyncSessionLocal
from app.models import TickData, TickSyncJob
from app.services.gateway_client import GatewayClient, GatewayError

logger = logging.getLogger("mt5quant.tick.sync")

_GATEWAY_FETCH_LIMIT = 100_000   # per gateway call cap (route allows 200k)
_INSERT_BATCH = 5_000            # rows per INSERT statement
_MAX_ERROR_LEN = 500


class TickSyncQueue:
    """Single-worker serial executor for :class:`TickSyncJob` rows."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._started = False

    # ── lifecycle ──────────────────────────────────────────────
    async def start(self) -> None:
        if self._started or not settings.TICK_SYNC_ENABLED:
            return
        self._started = True
        self._worker = asyncio.create_task(self._loop(), name="tick-sync-worker")
        logger.info("tick sync queue started")

    async def shutdown(self) -> None:
        self._started = False
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
        logger.info("tick sync queue stopped")

    async def startup_sweep(self) -> None:
        """Jobs left ``running`` by a previous process are failed outright."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                update(TickSyncJob)
                .where(TickSyncJob.status == "running")
                .values(status="failed", error="服务重启中断，请重新发起同步",
                        finished_at=datetime.now(UTC))
            )
            await db.commit()
            if result.rowcount:  # type: ignore[attr-defined]  # SQLAlchemy Result type annotation gap — real type has .rowcount/.scalars()
                logger.warning("tick sync sweep: %d orphaned running jobs failed",
                               result.rowcount)  # type: ignore[attr-defined]  # SQLAlchemy Result type annotation gap — real type has .rowcount/.scalars()

    def enqueue(self, job_id: str) -> None:
        self._queue.put_nowait(str(job_id))

    async def _loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self.process_job(job_id)
            except Exception:  # noqa: BLE001 — worker must survive any job
                logger.exception("tick sync job %s crashed the worker", job_id)

    # ── execution ──────────────────────────────────────────────
    async def process_job(self, job_id: str) -> None:
        """Run one sync job to a terminal state (also the test entry-point)."""
        async with AsyncSessionLocal() as db:
            job = await db.get(TickSyncJob, uuid_mod.UUID(job_id))
        if job is None or job.status != "pending":
            return

        async with AsyncSessionLocal() as db:
            await db.execute(
                update(TickSyncJob)
                .where(TickSyncJob.id == job.id, TickSyncJob.status == "pending")
                .values(status="running", started_at=datetime.now(UTC))
            )
            await db.commit()

        total = 0
        error: str | None = None
        client = GatewayClient()
        try:
            chunk = settings.TICK_SYNC_CHUNK_SECONDS
            start = int(job.start_time.timestamp())
            end = int(job.end_time.timestamp())
            cursor = start
            while cursor < end:
                window_end = min(cursor + chunk, end)
                rows = await client.ticks_range(
                    job.symbol, cursor, window_end, _GATEWAY_FETCH_LIMIT
                )
                if len(rows) >= _GATEWAY_FETCH_LIMIT:
                    logger.warning(
                        "tick sync %s window [%d,%d) hit the fetch limit — "
                        "slice may be truncated; re-sync the overlap to fill gaps",
                        job_id, cursor, window_end,
                    )
                total += await self._upsert_rows(job.symbol, rows)
                cursor = window_end
        except GatewayError as exc:
            error = exc.detail[:_MAX_ERROR_LEN]
        except Exception as exc:  # noqa: BLE001 — record and surface as failed
            error = f"{exc.__class__.__name__}: {exc}"[:_MAX_ERROR_LEN]
        finally:
            await client.aclose()

        async with AsyncSessionLocal() as db:
            await db.execute(
                update(TickSyncJob)
                .where(TickSyncJob.id == job.id, TickSyncJob.status == "running")
                .values(
                    status="failed" if error else "done",
                    error=error,
                    tick_count=total,
                    finished_at=datetime.now(UTC),
                )
            )
            await db.commit()
        logger.info("tick sync job %s → %s (%d rows)", job_id,
                    "failed" if error else "done", total)

    @staticmethod
    async def _upsert_rows(symbol: str, rows: list[dict]) -> int:
        """Conflict-safe bulk insert; returns the number of NEW rows stored."""
        if not rows:
            return 0
        inserted = 0
        async with AsyncSessionLocal() as db:
            for i in range(0, len(rows), _INSERT_BATCH):
                batch = [
                    {
                        "symbol": symbol,
                        "time_msc": int(r["time_msc"]),
                        "bid": float(r["bid"]),
                        "ask": float(r["ask"]),
                        "last": float(r["last"]),
                        "volume": int(r["volume"]),
                        "flags": int(r["flags"]),
                    }
                    for r in rows[i:i + _INSERT_BATCH]
                ]
                stmt = pg_insert(TickData).values(batch).on_conflict_do_nothing(
                    index_elements=["symbol", "time_msc"]
                )
                result = await db.execute(stmt)
                inserted += result.rowcount or 0  # type: ignore[attr-defined]  # SQLAlchemy Result type annotation gap — real type has .rowcount/.scalars()
            await db.commit()
        return inserted

    @staticmethod
    async def purge_symbol(symbol: str) -> int:
        """Delete every stored tick for ``symbol``; returns rows removed."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(delete(TickData).where(TickData.symbol == symbol))
            await db.commit()
            return result.rowcount or 0  # type: ignore[attr-defined]  # SQLAlchemy Result type annotation gap — real type has .rowcount/.scalars()


_queue: TickSyncQueue | None = None


def get_tick_sync_queue() -> TickSyncQueue:
    global _queue
    if _queue is None:
        _queue = TickSyncQueue()
    return _queue


async def coverage() -> list[dict]:
    """Per-symbol stored tick coverage: row count + min/max time_msc."""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(
                TickData.symbol,
                func.count().label("tick_count"),
                func.min(TickData.time_msc).label("min_time_msc"),
                func.max(TickData.time_msc).label("max_time_msc"),
            )
            .group_by(TickData.symbol)
            .order_by(TickData.symbol)
        )).all()
    return [
        {
            "symbol": r.symbol,
            "tick_count": int(r.tick_count),
            "min_time_msc": int(r.min_time_msc),
            "max_time_msc": int(r.max_time_msc),
        }
        for r in rows
    ]
