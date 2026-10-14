"""Strategy marketplace endpoints (PRD Phase 3): publish / browse / detail /
remove / import.

Route prefix is assigned in ``router.py`` (``/market/listings``) — the plain
``/market`` prefix belongs to the data-center market routes, so this module
is intentionally named ``marketplace``.

Semantics:
- A listing is a **frozen snapshot** of the source strategy at publish time
  (later strategy edits never leak into the marketplace).
- Listings are visible to every authenticated user; cross-user access to
  mutations → 404 (never 403, do not expose existence).
- Removal is a soft delete (``status="removed"``) and idempotent for the
  author; removed listings disappear from browse/detail.
- Import creates a fresh draft strategy owned by the importer and bumps
  ``import_count`` in the same transaction.
- Legal boundary (仅工具分享，不涉及信号售卖): publishing requires
  ``disclaimer_ack`` (schema-level 422), every mutation is audited.
"""

from __future__ import annotations

import hashlib
import math
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.dependencies import CurrentUser, DBSession
from app.models import MarketListing, Strategy, User
from app.schemas.marketplace import (
    ListingCreate,
    ListingDetailOut,
    ListingImportRequest,
    ListingListResponse,
    ListingOut,
)
from app.schemas.strategy import StrategyOut
from app.services.audit import AuditActor
from app.services.audit import record as audit_record
from app.services.strategy_validator import validate_source

router = APIRouter()

_PAGE_SIZE_MAX = 100


def _user_id(current: dict) -> uuid.UUID:
    return uuid.UUID(current["sub"])


def _actor(current: dict, request: Request | None = None) -> AuditActor:
    ip = request.client.host if request is not None and request.client else ""
    return AuditActor(actor_type="user", actor_id=_user_id(current),
                      actor_label=str(current.get("email") or current["sub"])[:120],
                      source_ip=ip or "")


def _source_hash(code: str) -> str:
    return hashlib.sha256((code or "").encode("utf-8")).hexdigest()[:16]


async def _username_map(db, listings: list[MarketListing]) -> dict[uuid.UUID, str]:
    """Batch-resolve author usernames (author may be gone → empty string)."""
    ids = {row.author_id for row in listings}
    if not ids:
        return {}
    rows = await db.execute(select(User.id, User.username).where(User.id.in_(ids)))
    return {uid: name or "" for uid, name in rows.all()}


@router.post("", response_model=ListingDetailOut,
             status_code=status.HTTP_201_CREATED)
async def publish_listing(
    payload: ListingCreate, current: CurrentUser, db: DBSession, request: Request
) -> ListingDetailOut:
    strategy = await db.get(Strategy, payload.strategy_id)
    if strategy is None or str(strategy.user_id) != current["sub"]:
        raise HTTPException(status_code=404, detail="策略不存在")

    report = validate_source(strategy.source_code)
    if not report.ok:
        raise HTTPException(status_code=422, detail=report.model_dump())

    src_hash = _source_hash(strategy.source_code)
    dup = (await db.execute(
        select(MarketListing).where(
            MarketListing.author_id == strategy.user_id,
            MarketListing.source_hash == src_hash,
            MarketListing.status == "active",
        ).limit(1)
    )).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(
            status_code=409, detail="已发布过相同内容的策略，请先下架旧条目或修改源码"
        )

    listing = MarketListing(
        strategy_id=strategy.id,
        author_id=strategy.user_id,
        strategy_name=strategy.name,
        description=strategy.description,
        language=strategy.language,
        direction=strategy.direction,
        symbol_ids=list(strategy.symbol_ids),
        timeframe=strategy.timeframe,
        tags=list(strategy.tags),
        source_code=strategy.source_code,
        source_hash=src_hash,
    )
    db.add(listing)
    await db.commit()
    await db.refresh(listing)
    author = await db.get(User, listing.author_id)
    await audit_record(
        "market.listing_publish", actor=_actor(current, request),
        target_type="market_listing", target_id=str(listing.id),
        params={"strategy_id": str(strategy.id),
                "strategy_name": listing.strategy_name,
                "source_hash": src_hash},
    )
    return ListingDetailOut.model_validate(
        {**listing.to_dict(),
         "author_username": author.username if author else ""}
    )


@router.get("", response_model=ListingListResponse)
async def list_listings(
    current: CurrentUser,
    db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=_PAGE_SIZE_MAX),
    symbol: str = Query(default="", max_length=32),
    timeframe: str | None = Query(default=None),
    q: str = Query(default="", max_length=80),
    sort: str = Query(default="latest"),
) -> ListingListResponse:
    filters = [MarketListing.status == "active"]
    if symbol:
        filters.append(MarketListing.symbol_ids.contains([symbol.upper()]))
    if timeframe:
        if timeframe not in ("M1", "M5", "M15", "M30", "H1", "H4", "D1"):
            raise HTTPException(status_code=422, detail="无效的周期筛选值")
        filters.append(MarketListing.timeframe == timeframe)
    if q:
        filters.append(MarketListing.strategy_name.ilike(f"%{q}%"))
    if sort not in ("latest", "popular"):
        raise HTTPException(status_code=422, detail="无效的排序值")
    order_by = (
        [MarketListing.import_count.desc(), MarketListing.created_at.desc()]
        if sort == "popular"
        else [MarketListing.created_at.desc()]
    )

    total = (
        await db.execute(
            select(func.count()).select_from(MarketListing).where(*filters)
        )
    ).scalar_one()
    result = await db.execute(
        select(MarketListing)
        .where(*filters)
        .order_by(*order_by)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    listings = list(result.scalars())
    names = await _username_map(db, listings)
    return ListingListResponse(
        items=[
            ListingOut.model_validate(
                {**row.to_dict(), "author_username": names.get(row.author_id, "")}
            )
            for row in listings
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(math.ceil(total / page_size), 1),
    )


async def _get_active(db, listing_id: uuid.UUID) -> MarketListing:
    listing = await db.get(MarketListing, listing_id)
    if listing is None or listing.status != "active":
        raise HTTPException(status_code=404, detail="市场条目不存在")
    return listing


@router.get("/{listing_id}", response_model=ListingDetailOut)
async def get_listing(
    listing_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> ListingDetailOut:
    listing = await _get_active(db, listing_id)
    names = await _username_map(db, [listing])
    return ListingDetailOut.model_validate(
        {**listing.to_dict(),
         "author_username": names.get(listing.author_id, "")}
    )


@router.delete("/{listing_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_listing(
    listing_id: uuid.UUID, current: CurrentUser, db: DBSession, request: Request
) -> None:
    listing = await db.get(MarketListing, listing_id)
    if listing is None or listing.author_id != _user_id(current):
        raise HTTPException(status_code=404, detail="市场条目不存在")
    if listing.status == "removed":
        return  # idempotent — no re-audit
    listing.status = "removed"
    await db.commit()
    await audit_record(
        "market.listing_remove", actor=_actor(current, request),
        target_type="market_listing", target_id=str(listing.id),
        params={"strategy_name": listing.strategy_name},
    )


@router.post("/{listing_id}/import", response_model=StrategyOut,
             status_code=status.HTTP_201_CREATED)
async def import_listing(
    listing_id: uuid.UUID,
    payload: ListingImportRequest,
    current: CurrentUser,
    db: DBSession,
    request: Request,
) -> Strategy:
    listing = await _get_active(db, listing_id)

    # Defensive re-validation: the frozen row may have been tampered with
    # outside the API (direct DB edit) — never import invalid code.
    report = validate_source(listing.source_code)
    if not report.ok:
        raise HTTPException(status_code=422, detail=report.model_dump())

    user_id = _user_id(current)
    strategy = Strategy(
        user_id=user_id,
        name=payload.name,
        description=listing.description,
        language=listing.language,
        direction=listing.direction,
        symbol_ids=list(listing.symbol_ids),
        timeframe=listing.timeframe,
        tags=list(listing.tags),
        source_code=listing.source_code,
        builder_config=None,
        created_by=user_id,
    )
    db.add(strategy)
    listing.import_count += 1
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="同名策略已存在，请更换名称后重试"
        ) from None
    await db.refresh(strategy)
    await audit_record(
        "market.listing_import", actor=_actor(current, request),
        target_type="market_listing", target_id=str(listing.id),
        params={"strategy_id": str(strategy.id), "name": strategy.name,
                "import_count": listing.import_count},
    )
    return strategy
