"""
Strategy template seed sync.

Keeps ``strategy_templates`` in sync with ``shared/sdk/templates`` (single
source of truth).  Runs at application startup: new templates are inserted,
existing ones are updated in place (keyed by the unique ``name``) so that
schema/param changes made in the SDK land on already-initialized databases.

Template rows are built-in and read-only from the API — users never edit
them — so a blanket upsert is safe.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.engine.session import AsyncSessionLocal
from app.models import StrategyTemplate

# Mirror the bootstrap in app/sandbox/__init__.py: put <repo>/shared on sys.path
_SHARED_DIR = Path(__file__).resolve().parents[2] / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

from sdk.templates import seed_rows  # noqa: E402  (needs the sys.path shim)

# Columns refreshed on conflict (id / created_at stay stable per name)
_UPDATABLE = ("description", "language", "category", "sort_order", "parameters", "source_code")


async def sync_strategy_templates() -> dict[str, int]:
    """Upsert every template from ``seed_rows()``; returns inserted/updated/total."""
    rows = [dict(r) for r in seed_rows()]

    async with AsyncSessionLocal() as session:
        existing = set(
            await session.scalars(select(StrategyTemplate.name))
        )
        inserted = updated = 0
        for row in rows:
            stmt = pg_insert(StrategyTemplate).values(**row)
            stmt = stmt.on_conflict_do_update(
                index_elements=[StrategyTemplate.name],
                set_={col: getattr(stmt.excluded, col) for col in _UPDATABLE},
            )
            await session.execute(stmt)
            if row["name"] in existing:
                updated += 1
            else:
                inserted += 1
        await session.commit()

    return {"inserted": inserted, "updated": updated, "total": len(rows)}
