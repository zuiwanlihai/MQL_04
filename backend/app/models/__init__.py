"""
SQLAlchemy ORM models for the MT5 Quant Platform.

Import all models here so Alembic's ``env.py`` can call ``Base.metadata`` and
automatically discover every table.
"""

from app.models.alert import AlertEvent
from app.models.alert_rule import AlertRule
from app.models.analysis import BacktestAnalysis
from app.models.audit import AuditLog
from app.models.backtest import (
    BacktestLog,
    BacktestReport,
    BacktestTask,
    BacktestTrade,
)
from app.models.base import Base, BaseModel
from app.models.gateway_health_log import GatewayHealthLog
from app.models.gateway_node import GatewayNode
from app.models.kline_cache import KlineCache
from app.models.live import LiveLog, LiveRun, LiveTrade
from app.models.market_history import MarketDataRange
from app.models.market_listing import MarketListing
from app.models.mt5_account import MT5Account
from app.models.notebook import Notebook
from app.models.notify_channel import NotificationChannel
from app.models.paper import PaperLog, PaperRun, PaperTrade
from app.models.paper_stats import PaperDailyStats
from app.models.portfolio import PortfolioBacktest
from app.models.risk import RiskEvent, RiskRule
from app.models.risk_position import RiskPositionLog
from app.models.strategy import Strategy, StrategyTemplate, StrategyVersion
from app.models.synthetic_dataset import SyntheticDataset
from app.models.tick import TickData, TickSyncJob
from app.models.user import User
from app.models.user_session import UserSession

# -- Import concrete models below as they are added to the project -----------
# from app.models.trade import Trade

__all__ = [
    "AlertEvent",
    "AlertRule",
    "AuditLog",
    "Base",
    "BaseModel",
    "BacktestAnalysis",
    "BacktestLog",
    "BacktestReport",
    "BacktestTask",
    "BacktestTrade",
    "GatewayHealthLog",
    "GatewayNode",
    "KlineCache",
    "LiveLog",
    "LiveRun",
    "LiveTrade",
    "MT5Account",
    "MarketDataRange",
    "MarketListing",
    "Notebook",
    "NotificationChannel",
    "PaperDailyStats",
    "PaperLog",
    "PaperRun",
    "PaperTrade",
    "PortfolioBacktest",
    "RiskEvent",
    "RiskPositionLog",
    "RiskRule",
    "Strategy",
    "StrategyTemplate",
    "StrategyVersion",
    "SyntheticDataset",
    "TickData",
    "TickSyncJob",
    "User",
    "UserSession",
]
