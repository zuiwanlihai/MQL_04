"""Synthetic dataset generation API — backend-side numpy GBM + Parquet.

The previous flow required the frontend to POST the ENTIRE bars array
(34MB JSON for 13-month M1).  Now the frontend only posts the generation
parameters (symbol, base_price, volatility, start/end), and the backend
produces + persists Parquet in <2s.

This keeps the old CRUD endpoints for backward-compat (still accepts
bars=...  POSTs for legacy clients), but the new ``/generate`` endpoint is
the preferred path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.models.synthetic_dataset import SyntheticDataset
from app.schemas.synthetic import (
    SyntheticDatasetCreate,
    SyntheticDatasetDetail,
    SyntheticDatasetGenerateIn,
    SyntheticDatasetOut,
    SyntheticDatasetUpdate,
)
from app.services.synthetic.generator import (
    GeneratedM1,
    generate_m1_to_parquet,
    storage_path_for_id,
    write_bars_to_parquet,
)


router = APIRouter()


# ────────────────────────────────────────────────────────────────────────────
# NEW: backend-side generation (preferred)
# ────────────────────────────────────────────────────────────────────────────

@router.post("/generate", response_model=SyntheticDatasetOut, status_code=status.HTTP_201_CREATED)
async def generate_dataset(payload: SyntheticDatasetGenerateIn, db: AsyncSession = Depends(get_db)):
    """Generate synthetic M1 bars server-side and persist as Parquet.

    Two paths, both ending in a Parquet file:

    * **Parameter path** (simple shapes — random / uptrend / downtrend).
      Only generation parameters are posted; the backend runs numpy GBM
      producing the bars.  Request body ≈ 200 bytes, generation < 2s.
      ``bars`` must be ``None`` (and ``first_time`` / ``last_time``
      must both be supplied).

    * **Bars path** (complex shapes — V / W / M / inverted‑V, etc.).
      The client already produced bars locally and posts them here; the
      backend simply writes them to Parquet.  ``drift`` / ``seed`` /
      ``first_time`` / ``last_time`` are ignored — they come from the
      bars themselves.

    In both cases the DB row stores the Parquet path (``storage_path``)
    and the legacy JSONB ``bars`` column is left NULL.
    """
    # ── Validate exclusivity ─────────────────────────────────────────────
    if payload.bars is None:
        if payload.first_time is None or payload.last_time is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "参数模式下 first_time 和 last_time 必填；若已在前端生成好 bars，请直接传 bars 字段",
            )
    else:
        if len(payload.bars) == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bars 列表不能为空")
        if len(payload.bars) > 1_300_000:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"bars 上限 1300000，当前 {len(payload.bars)}",
            )

    # 1) Insert placeholder row to get a stable id
    ds = SyntheticDataset(
        name=payload.name,
        description=payload.description,
        symbol=payload.symbol,
        base_price=payload.base_price,
        volatility=payload.volatility,
        first_time=0,
        last_time=0,
        bar_count=0,
        bars=None,               # Parquet path — legacy JSONB stays NULL
        storage_path=None,       # filled after write
    )
    db.add(ds)
    await db.flush()            # get ds.id

    # 2) Write Parquet under SYNTHETIC_RUNTIME_DIR
    runtime_dir = Path(settings.SYNTHETIC_RUNTIME_DIR)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    rel_path = storage_path_for_id(ds.id)

    try:
        if payload.bars is not None:
            # ── Bars path: client already generated, just persist ──
            result: GeneratedM1 = write_bars_to_parquet(
                payload.bars,
                out_path=runtime_dir / rel_path,
            )
        else:
            # ── Parameter path: backend GBM ──
            result = generate_m1_to_parquet(
                out_path=runtime_dir / rel_path,
                start_ts=payload.first_time,
                end_ts=payload.last_time,
                base_price=payload.base_price,
                volatility=payload.volatility,
                drift=payload.drift,
                seed=payload.seed,
            )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    # 3) Update row with metadata from the Parquet write result
    ds.bar_count = result.bar_count
    ds.first_time = result.first_time
    ds.last_time = result.last_time
    ds.storage_path = rel_path
    await db.commit()
    await db.refresh(ds)

    return ds


# ────────────────────────────────────────────────────────────────────────────
# Legacy create (kept for compat — accepts bars array, still writes JSONB)
# ────────────────────────────────────────────────────────────────────────────

@router.post("", response_model=SyntheticDatasetOut, status_code=status.HTTP_201_CREATED)
async def create_dataset(payload: SyntheticDatasetCreate, db: AsyncSession = Depends(get_db)):
    if not payload.bars:
        raise HTTPException(400, "bars 不能为空")
    # 上限：130 万根（与前端 MAX_M1_BARS 和 BACKTEST_MAX_BARS 对齐）
    if len(payload.bars) > 1_300_000:
        raise HTTPException(400, f"bars 上限 1300000，当前 {len(payload.bars)}")

    ds = SyntheticDataset(
        name=payload.name,
        description=payload.description,
        symbol=payload.symbol,
        base_price=payload.base_price,
        volatility=payload.volatility,
        first_time=payload.first_time,
        last_time=payload.last_time,
        bar_count=payload.bar_count,
        bars=payload.bars,
        storage_path=None,       # legacy 路径：bars 存 JSONB，Parquet 路径为 None
    )
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    return ds


# ────────────────────────────────────────────────────────────────────────────
# List / detail with optional bars (summary-only by default)
# ────────────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[SyntheticDatasetOut])
async def list_datasets(
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    rows = await db.execute(
        select(SyntheticDataset)
        .order_by(SyntheticDataset.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(rows.scalars().all())


@router.get("/{ds_id}", response_model=SyntheticDatasetDetail)
async def get_dataset(
    ds_id: int,
    include_bars: bool = Query(
        default=False,
        description="Include raw bars array in response. "
                    "For large datasets (> 50K bars), set to true only when needed.",
    ),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(SyntheticDataset, ds_id)
    if not row:
        raise HTTPException(404, "数据集不存在")

    # By default, omit bars to avoid serialising a 34MB JSON payload.
    # Callers that need bars can set ?include_bars=true — but for
    # Parquet-backed datasets we advise using the backend generation
    # endpoint directly instead.
    if not include_bars:
        row.bars = None

    return row


@router.patch("/{ds_id}", response_model=SyntheticDatasetOut)
async def update_dataset(
    ds_id: int,
    payload: SyntheticDatasetUpdate,
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(SyntheticDataset, ds_id)
    if not row:
        raise HTTPException(404, "数据集不存在")
    for field, val in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, val)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/{ds_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(ds_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(SyntheticDataset, ds_id)
    if not row:
        raise HTTPException(404, "数据集不存在")

    # Clean up Parquet file if present
    if row.storage_path:
        p = Path(row.storage_path)
        if not p.is_absolute():
            p = Path(settings.SYNTHETIC_RUNTIME_DIR) / p
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass   # best-effort; don't block delete

    await db.delete(row)
    await db.commit()
