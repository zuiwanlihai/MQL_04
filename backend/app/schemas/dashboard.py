"""Dashboard aggregation schemas (M7).

Single read-only payload for the landing page: account mirror, today's P&L,
intraday equity curve, open positions, running-strategy cards, gateway health
and the latest alerts (PRD M7 仪表盘).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class DashboardAccount(BaseModel):
    id: uuid.UUID
    login: int
    name: str
    account_type: str
    currency: str
    balance: float
    equity: float
    margin_level: float
    is_connected: bool
    trade_frozen: bool = False
    live_allowed: bool = False


class DashboardTotals(BaseModel):
    equity: float = 0.0
    today_pnl: float = Field(default=0.0, description="运行中实盘 Σ(equity-day_start_equity)")
    unrealized_pnl: float = 0.0
    running_live: int = 0
    running_paper: int = 0
    demoted_live: int = 0


class EquityPoint(BaseModel):
    time: str
    equity: float
    balance: float


class DashboardPosition(BaseModel):
    run_id: uuid.UUID
    run_symbol: str
    ticket: int = 0
    symbol: str = ""
    type: str = ""
    volume: float = 0.0
    price_open: float = 0.0
    price_current: float = 0.0
    profit: float = 0.0
    swap: float = 0.0
    time_msc: int = 0


class DashboardLiveRun(BaseModel):
    id: uuid.UUID
    strategy_id: uuid.UUID
    strategy_name: str
    status: str
    symbol: str
    timeframe: str
    equity: float
    unrealized_pnl: float
    today_pnl: float = 0.0
    demoted: bool
    no_new_orders: bool
    reconcile_pending: bool
    observation_ends_at: datetime | None
    capital_cap_pct: int
    last_bar_time: datetime | None
    started_at: datetime | None
    account_type: str | None = None


class DashboardGateway(BaseModel):
    id: uuid.UUID | None = None
    name: str = ""
    status: str = "offline"
    version: str = ""
    uptime_seconds: float = 0.0
    active_sessions: int = 0
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    last_heartbeat: datetime | None = None


class DashboardAlert(BaseModel):
    id: uuid.UUID
    level: str
    source: str
    title: str
    message: str
    run_id: uuid.UUID | None
    is_read: bool
    created_at: datetime


class DashboardOut(BaseModel):
    accounts: list[DashboardAccount] = Field(default_factory=list)
    totals: DashboardTotals = Field(default_factory=DashboardTotals)
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    positions: list[DashboardPosition] = Field(default_factory=list)
    live_runs: list[DashboardLiveRun] = Field(default_factory=list)
    gateway: DashboardGateway = Field(default_factory=DashboardGateway)
    alerts: list[DashboardAlert] = Field(default_factory=list)
    alerts_unread: int = 0
