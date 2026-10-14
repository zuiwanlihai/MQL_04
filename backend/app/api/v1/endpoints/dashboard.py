"""Dashboard aggregation endpoint (M7, PRD 监控与告警).

Single read-only request that fills the landing page: bound-account mirror,
today's P&L, intraday equity curve, open positions, running-strategy cards,
primary-gateway health and the latest alerts.  Everything is scoped to the
current user (same ownership model as live/paper: cross-user → excluded).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import func, select

from app.dependencies import CurrentUser, DBSession
from app.models import (
    AlertEvent,
    GatewayNode,
    LiveRun,
    MT5Account,
    PaperRun,
    Strategy,
)
from app.schemas.dashboard import (
    DashboardAccount,
    DashboardAlert,
    DashboardGateway,
    DashboardLiveRun,
    DashboardOut,
    DashboardPosition,
    DashboardTotals,
    EquityPoint,
)

router = APIRouter()

_ACTIVE_LIVE = ("pending", "running", "paused")


def _user_id(current: dict) -> uuid.UUID:
    return uuid.UUID(current["sub"])


def _today_start_utc(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _f(v: object) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


@router.get("/overview", response_model=DashboardOut)
async def overview(current: CurrentUser, db: DBSession) -> DashboardOut:
    uid = _user_id(current)
    now = datetime.now(UTC)
    day_start = _today_start_utc(now)

    # ── accounts (owned) ──
    accts = (await db.execute(
        select(MT5Account).where(MT5Account.user_id == uid).order_by(MT5Account.created_at)
    )).scalars().all()
    accounts = [
        DashboardAccount(
            id=a.id, login=a.login, name=a.name, account_type=a.account_type,
            currency=a.currency, balance=_f(a.balance), equity=_f(a.equity),
            margin_level=_f(a.margin_level), is_connected=a.is_connected,
            trade_frozen=a.trade_frozen, live_allowed=a.live_allowed,
        ) for a in accts
    ]

    # ── live runs (active + demoted) with strategy names ──
    live_rows = (await db.execute(
        select(LiveRun).where(LiveRun.user_id == uid,
                              LiveRun.status.in_(_ACTIVE_LIVE))
        .order_by(LiveRun.created_at.desc())
    )).scalars().all()
    strat_ids = {r.strategy_id for r in live_rows}
    name_map: dict[uuid.UUID, str] = {}
    if strat_ids:
        srows = (await db.execute(
            select(Strategy.id, Strategy.name).where(Strategy.id.in_(strat_ids))
        )).all()
        name_map = {row.id: row.name for row in srows}
    acct_ids = {r.account_id for r in live_rows if r.account_id}
    acct_type_map: dict[uuid.UUID, str | None] = {}
    if acct_ids:
        arows = (await db.execute(
            select(MT5Account.id, MT5Account.account_type).where(MT5Account.id.in_(acct_ids))
        )).all()
        acct_type_map = {row.id: row.account_type for row in arows}

    totals_equity = 0.0
    totals_today = 0.0
    totals_unreal = 0.0
    demoted = 0
    live_cards: list[DashboardLiveRun] = []
    positions: list[DashboardPosition] = []
    curve: list[EquityPoint] = []

    for r in live_rows:
        eq = _f(r.equity)
        today = eq - _f(r.day_start_equity) if r.day_start_date == now.strftime("%Y-%m-%d") else 0.0
        unreal = _f(r.unrealized_pnl)
        totals_today += today
        totals_unreal += unreal
        if r.demoted:
            demoted += 1
        live_cards.append(DashboardLiveRun(
            id=r.id, strategy_id=r.strategy_id,
            strategy_name=name_map.get(r.strategy_id, "—"),
            status=r.status, symbol=r.symbol, timeframe=r.timeframe,
            equity=eq, unrealized_pnl=unreal, today_pnl=today,
            demoted=r.demoted, no_new_orders=r.no_new_orders,
            reconcile_pending=r.reconcile_pending,
            observation_ends_at=r.observation_ends_at,
            capital_cap_pct=r.capital_cap_pct,
            last_bar_time=r.last_bar_time, started_at=r.started_at,
            account_type=acct_type_map.get(r.account_id),
        ))
        for p in (r.positions or []):
            positions.append(DashboardPosition(
                run_id=r.id, run_symbol=r.symbol,
                ticket=int(p.get("ticket", 0) or 0),
                symbol=str(p.get("symbol", "")),
                type=str(p.get("type", "")),
                volume=_f(p.get("volume", 0)),
                price_open=_f(p.get("price_open", 0)),
                price_current=_f(p.get("price_current", 0)),
                profit=_f(p.get("profit", 0)),
                swap=_f(p.get("swap", 0)),
                time_msc=int(p.get("time", 0) or 0),
            ))
        for pt in (r.equity_history or []):
            t = pt.get("time")
            if not t:
                continue
            dt = datetime.fromisoformat(t) if isinstance(t, str) else t
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            if dt >= day_start:
                curve.append(EquityPoint(time=dt.isoformat(),
                                         equity=_f(pt.get("equity", 0)),
                                         balance=_f(pt.get("balance", 0))))

    running_live = sum(1 for r in live_rows if r.status == "running")
    # headline equity = real account mirror (sum of owned accounts); the
    # per-run equity on the cards is the same broker account, not additive.
    totals_equity = sum(_f(a.equity) for a in accts)

    # ── paper runs (running count + fallback curve when no live data) ──
    paper_running = (await db.execute(
        select(func.count()).select_from(PaperRun).where(
            PaperRun.user_id == uid, PaperRun.status == "running")
    )).scalar_one()
    if not curve:
        prows = (await db.execute(
            select(PaperRun).where(PaperRun.user_id == uid,
                                   PaperRun.status.in_(_ACTIVE_LIVE))
        )).scalars().all()
        for pr in prows:
            for pt in (pr.equity_history or []):
                t = pt.get("time")
                if not t:
                    continue
                dt = datetime.fromisoformat(t) if isinstance(t, str) else t
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                if dt >= day_start:
                    curve.append(EquityPoint(time=dt.isoformat(),
                                             equity=_f(pt.get("equity", 0)),
                                             balance=_f(pt.get("balance", 0))))
    curve.sort(key=lambda x: x.time)

    totals = DashboardTotals(
        equity=round(totals_equity, 2), today_pnl=round(totals_today, 2),
        unrealized_pnl=round(totals_unreal, 2), running_live=running_live,
        running_paper=paper_running, demoted_live=demoted,
    )

    # ── primary gateway ──
    node = (await db.execute(
        select(GatewayNode).where(GatewayNode.is_primary.is_(True))
        .order_by(GatewayNode.created_at).limit(1)
    )).scalars().first()
    if node is None:
        node = (await db.execute(
            select(GatewayNode).order_by(GatewayNode.created_at).limit(1)
        )).scalars().first()
    gateway = DashboardGateway(
        id=node.id if node else None, name=node.name if node else "",
        status=node.status if node else "offline",
        version=node.version if node else "",
        uptime_seconds=node.uptime_seconds if node else 0.0,
        active_sessions=node.active_sessions if node else 0,
        cpu_percent=node.cpu_percent if node else 0.0,
        memory_percent=node.memory_percent if node else 0.0,
        last_heartbeat=node.last_heartbeat if node else None,
    )

    # ── alerts ──
    alerts = (await db.execute(
        select(AlertEvent).order_by(AlertEvent.created_at.desc()).limit(10)
    )).scalars().all()
    unread = (await db.execute(
        select(func.count()).select_from(AlertEvent).where(AlertEvent.is_read.is_(False))
    )).scalar_one()
    alert_items = [
        DashboardAlert(id=a.id, level=a.level, source=a.source, title=a.title,
                       message=a.message, run_id=a.run_id, is_read=a.is_read,
                       created_at=a.created_at) for a in alerts
    ]

    return DashboardOut(
        accounts=accounts, totals=totals, equity_curve=curve,
        positions=positions, live_runs=live_cards, gateway=gateway,
        alerts=alert_items, alerts_unread=unread,
    )
