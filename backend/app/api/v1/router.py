"""
API v1 top-level router.

Aggregates all sub-routers (auth, users, strategies, backtests, …) and
mounts them under a single ``APIRouter`` so ``main.py`` only needs one
``include_router`` call.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints.accounts import router as accounts_router
from app.api.v1.endpoints.alert_rules import router as alert_rules_router
from app.api.v1.endpoints.audit import router as audit_router
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.backtests import router as backtests_router
from app.api.v1.endpoints.dashboard import router as dashboard_router
from app.api.v1.endpoints.ea_bridge import router as ea_bridge_router
from app.api.v1.endpoints.gateway import router as gateway_router
from app.api.v1.endpoints.kline_cache import router as kline_cache_router
from app.api.v1.endpoints.live import router as live_router
from app.api.v1.endpoints.market import router as market_router
from app.api.v1.endpoints.market_history import router as market_history_router
from app.api.v1.endpoints.marketplace import router as marketplace_router
from app.api.v1.endpoints.notebooks import router as notebooks_router
from app.api.v1.endpoints.notify import router as notify_router
from app.api.v1.endpoints.paper import router as paper_router
from app.api.v1.endpoints.strategies import router as strategies_router
from app.api.v1.endpoints.synthetic_datasets import router as synthetic_datasets_router
from app.api.v1.endpoints.ticks import router as ticks_router
from app.api.v1.endpoints.trade import router as trade_router

v1_router = APIRouter(prefix="")


# ---------------------------------------------------------------------------
# Placeholder routes — demonstrate the router is working
# ---------------------------------------------------------------------------
@v1_router.get("/ping", tags=["health"])
async def ping():
    """Quick connectivity check under the versioned API prefix."""
    return {"ping": "pong"}


# ---------------------------------------------------------------------------
# Active sub-routers
# ---------------------------------------------------------------------------
v1_router.include_router(auth_router, prefix="/auth", tags=["auth"])
v1_router.include_router(market_router, prefix="/market", tags=["market"])
v1_router.include_router(
    market_history_router, prefix="/market/history", tags=["market"]
)
v1_router.include_router(
    marketplace_router, prefix="/market/listings", tags=["marketplace"]
)
v1_router.include_router(ticks_router, prefix="/ticks", tags=["ticks"])
v1_router.include_router(kline_cache_router, prefix="/kline-cache", tags=["kline-cache"])
v1_router.include_router(accounts_router, prefix="/accounts", tags=["accounts"])
v1_router.include_router(gateway_router, prefix="/gateway", tags=["gateway"])
v1_router.include_router(ea_bridge_router, prefix="/ea", tags=["ea-bridge"])
v1_router.include_router(strategies_router, prefix="/strategies", tags=["strategies"])
v1_router.include_router(synthetic_datasets_router, prefix="/synthetic-datasets", tags=["synthetic-datasets"])
v1_router.include_router(backtests_router, prefix="/backtests", tags=["backtests"])
v1_router.include_router(paper_router, prefix="/paper", tags=["paper"])
v1_router.include_router(live_router, prefix="/live", tags=["live"])
v1_router.include_router(trade_router, prefix="/trade", tags=["trade"])
v1_router.include_router(audit_router, prefix="/audit", tags=["audit"])
v1_router.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])
v1_router.include_router(notify_router, prefix="/notify", tags=["notify"])
v1_router.include_router(alert_rules_router, prefix="/alert-rules", tags=["alert-rules"])
v1_router.include_router(notebooks_router, prefix="/notebooks", tags=["notebooks"])

# ---------------------------------------------------------------------------
# Sub-routers — uncomment and register as new features are built
# ---------------------------------------------------------------------------
# from app.api.v1.endpoints.users import router as users_router
# from app.api.v1.endpoints.strategies import router as strategies_router
# from app.api.v1.endpoints.backtests import router as backtests_router
# from app.api.v1.endpoints.observations import router as observations_router
#
# v1_router.include_router(users_router,      prefix="/users",      tags=["users"])
# v1_router.include_router(strategies_router, prefix="/strategies", tags=["strategies"])
# v1_router.include_router(backtests_router,  prefix="/backtests",  tags=["backtests"])
# v1_router.include_router(observations_router, prefix="/observations", tags=["observations"])
