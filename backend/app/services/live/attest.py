"""Nightly paper-trading attest aggregation (M6 admission ③ data source).

Runs once a day (00:05 UTC) and writes one ``paper_daily_stats`` row per
(strategy_version, UTC day) that had paper activity the day before.  Uptime is
proxied by ``bars_processed × tf_seconds`` (a paper run only advances on closed
bars, so processed bars × period ≈ live seconds); violations come from
RiskEvent(source=paper) and PaperRun failures carrying sandbox error codes.

Idempotent: re-running for the same day upserts the row.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.engine.session import AsyncSessionLocal
from app.models import PaperDailyStats, PaperLog, PaperRun, RiskEvent
from app.sandbox.matching import TF_SECONDS

logger = logging.getLogger("mt5quant.live.attest")

SANDBOX_CODES = ("SANDBOX_VIOLATION", "ILLEGAL_IMPORT", "RESOURCE_EXCEEDED",
                 "RESOURCE_TIMEOUT")


async def aggregate_day(day: date) -> int:
    """Upsert attest rows for ``day``; return the number of versions recorded."""
    day_start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    written = 0
    async with AsyncSessionLocal() as db:
        # paper runs that were alive at any point during the day
        runs = (await db.execute(
            select(PaperRun).where(
                PaperRun.started_at.isnot(None),
                PaperRun.started_at < day_end,
                (PaperRun.stopped_at.is_(None)) | (PaperRun.stopped_at >= day_start),
            )
        )).scalars().all()

        per_version: dict[tuple[object, int], dict] = {}
        for run in runs:
            tf = TF_SECONDS.get(run.timeframe, 3600)
            # bars processed on this day ≈ last_bar_time cursor within the day
            bars = await _bars_on_day(db, run.id, day_start, day_end)
            uptime = min(bars * tf, 86400)
            key = (run.strategy_id, run.strategy_version or 0)
            acc = per_version.setdefault(
                key, {"uptime": 0, "bars": 0, "risk": 0, "viol": 0}
            )
            acc["uptime"] += uptime
            acc["bars"] += bars
            if run.status == "failed" and (run.error_code or "") in SANDBOX_CODES:
                acc["viol"] += 1

        # risk events (source=paper) on this day
        risk_rows = (await db.execute(
            select(RiskEvent.paper_run_id, func.count())
            .where(RiskEvent.source == "paper",
                   RiskEvent.created_at >= day_start,
                   RiskEvent.created_at < day_end)
            .group_by(RiskEvent.paper_run_id)
        )).all()
        run_to_version = {
            (run.id): (run.strategy_id, run.strategy_version or 0) for run in runs
        }
        for pr_id, cnt in risk_rows:
            if pr_id is None:
                continue
            ver_key: tuple[uuid.UUID, int] | None = run_to_version.get(pr_id)
            if ver_key and ver_key in per_version:
                per_version[ver_key]["risk"] += cnt

        for (strategy_id, version), agg in per_version.items():
            stmt = pg_insert(PaperDailyStats).values(
                id=__import__("uuid").uuid4(),
                strategy_id=strategy_id,
                strategy_version=version,
                account_id=None,
                day=day,
                uptime_seconds=min(agg["uptime"], 86400),
                bars_processed=agg["bars"],
                risk_triggers=agg["risk"],
                sandbox_violations=agg["viol"],
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_paper_daily_stats_version_account_day",
                set_={
                    "uptime_seconds": stmt.excluded.uptime_seconds,
                    "bars_processed": stmt.excluded.bars_processed,
                    "risk_triggers": stmt.excluded.risk_triggers,
                    "sandbox_violations": stmt.excluded.sandbox_violations,
                    "updated_at": datetime.now(UTC),
                },
            )
            await db.execute(stmt)
            written += 1
        await db.commit()
    logger.info("paper attest aggregated for %s: %d versions", day, written)
    return written


async def _bars_on_day(db, run_id, day_start: datetime, day_end: datetime) -> int:
    """Count distinct closed bar_times the paper processed within the day.

    Uses the log stream's bar_time cursor (one line per processed bar is not
    guaranteed, so we count distinct bar_time values on signal/system lines).
    """
    n = (await db.execute(
        select(func.count(func.distinct(PaperLog.bar_time)))
        .where(
            PaperLog.run_id == run_id,
            PaperLog.bar_time >= day_start,
            PaperLog.bar_time < day_end,
            PaperLog.bar_time.isnot(None),
        )
    )).scalar_one()
    return int(n or 0)


async def attest_loop(stop_event) -> None:
    """Background task: aggregate the previous UTC day at ~00:05 every day.

    Wakes hourly (cheap) and fires once per day after midnight — resilient to
    backend restarts because ``aggregate_day`` is idempotent and a missed day
    is caught up by the daily catch-up loop below.
    """
    import asyncio

    last_run: date | None = None
    while not stop_event.is_set():
        now = datetime.now(UTC)
        target = (now - timedelta(days=1)).date()
        if now.hour >= 1 and last_run != target:
            # catch-up: aggregate every missing day since the last run
            day = last_run or (now - timedelta(days=7)).date()
            while day < target:
                day += timedelta(days=1)
                try:
                    await aggregate_day(day)
                except Exception:  # noqa: BLE001
                    logger.exception("attest aggregate %s failed", day)
            last_run = target
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            return
