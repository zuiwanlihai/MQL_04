"""
MT5 Quant Platform — FastAPI application factory and entry-point.

Usage (development)::

    uvicorn app.main:app --reload

"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.endpoints.market_ws import router as market_ws_router
from app.api.v1.router import v1_router
from app.config import settings
from app.engine import close_db, close_redis, init_db, init_redis
from app.services.alert_rules import rules_loop
from app.services.audit_patrol import patrol_loop
from app.services.backtest.queue import get_backtest_queue
from app.services.gateway_heartbeat import heartbeat_loop
from app.services.live.attest import attest_loop
from app.services.live.manager import get_live_manager
from app.services.live.reconcile import offline_watchdog, reconcile_loop
from app.services.market_history import run_sync_loop
from app.services.notebook_kernel import sweep_loop as notebook_sweep_loop
from app.services.paper.manager import get_paper_manager
from app.services.quote_push import (
    create_quote_hub,
    ping_loop,
    pump_loop,
    shutdown_quote_hub,
)
from app.services.tick_sync import get_tick_sync_queue

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("mt5quant")

# ---------------------------------------------------------------------------
# Lifespan (replaces deprecated ``on_event`` handlers)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan — start-up and shut-down logic."""
    logger.info("Starting MT5 Quant Platform v%s", settings.APP_VERSION)

    # Initialise infrastructure
    await init_db()
    await init_redis()

    # Strategy template seed sync (sdk/templates → strategy_templates, by name).
    # Non-fatal: a sync failure must not block the platform from starting.
    try:
        from app.seed import sync_strategy_templates

        stats = await sync_strategy_templates()
        logger.info("Strategy templates synced: %s", stats)
    except Exception:
        logger.exception("Strategy template sync failed (non-fatal)")

    heartbeat_task: asyncio.Task | None = None
    if settings.GATEWAY_HEARTBEAT_ENABLED:
        heartbeat_task = asyncio.create_task(heartbeat_loop())
        logger.info("Gateway heartbeat loop started (interval=%ss)",
                    settings.GATEWAY_HEARTBEAT_INTERVAL)

    rules_task: asyncio.Task | None = None
    if settings.ALERT_RULES_ENABLED:
        rules_task = asyncio.create_task(rules_loop(), name="alert-rules")
        logger.info("Alert-rule evaluation loop started (interval=%ss)",
                    settings.ALERT_RULES_INTERVAL_SECONDS)

    patrol_task: asyncio.Task | None = None
    if settings.AUDIT_PATROL_ENABLED:
        patrol_task = asyncio.create_task(patrol_loop(), name="audit-patrol")
        logger.info("Audit-chain patrol loop started (interval=%ss)",
                    settings.AUDIT_PATROL_INTERVAL_SECONDS)

    bt_queue = None
    if settings.BACKTEST_ENABLED:
        bt_queue = get_backtest_queue()
        await bt_queue.startup_sweep()
        await bt_queue.start()
        logger.info("Backtest queue started (concurrency=%s)",
                    settings.BACKTEST_MAX_CONCURRENCY)

    paper_mgr = None
    if settings.PAPER_ENABLED:
        paper_mgr = get_paper_manager()
        await paper_mgr.startup_sweep()
        logger.info("Paper trading manager ready (max concurrent=%s)",
                    settings.PAPER_MAX_CONCURRENT)

    live_mgr = None
    live_stop = None
    live_tasks: list[asyncio.Task] = []
    if settings.LIVE_ENABLED:
        live_mgr = get_live_manager()
        await live_mgr.startup_sweep()
        live_stop = asyncio.Event()
        live_tasks = [
            asyncio.create_task(reconcile_loop(live_stop), name="live-reconcile"),
            asyncio.create_task(offline_watchdog(live_stop), name="live-offline-watchdog"),
            asyncio.create_task(attest_loop(live_stop), name="live-paper-attest"),
        ]
        logger.info("Live trading manager ready (max concurrent=%s, "
                    "reconcile=%ss)", settings.LIVE_MAX_CONCURRENT,
                    settings.LIVE_RECONCILE_SECONDS)

    tick_queue = None
    if settings.TICK_SYNC_ENABLED:
        tick_queue = get_tick_sync_queue()
        await tick_queue.startup_sweep()
        await tick_queue.start()
        logger.info("Tick sync queue started")

    history_task: asyncio.Task | None = None
    if settings.MARKET_HISTORY_SYNC_ENABLED:
        history_task = asyncio.create_task(run_sync_loop(), name="market-history-sync")
        logger.info("Market history sync loop started (interval=%ss)",
                    settings.MARKET_HISTORY_SYNC_INTERVAL_SECONDS)

    nb_sweep_task: asyncio.Task | None = None
    if settings.NOTEBOOK_ENABLED:
        nb_sweep_task = asyncio.create_task(notebook_sweep_loop(), name="notebook-sweep")
        logger.info("Notebook session sweep loop started (ttl=%smin)",
                    settings.NOTEBOOK_SESSION_TTL_MINUTES)

    quote_tasks: list[asyncio.Task] = []
    if settings.MARKET_PUSH_ENABLED:
        quote_hub = create_quote_hub()
        quote_tasks = [
            asyncio.create_task(pump_loop(quote_hub), name="quote-push-pump"),
            asyncio.create_task(ping_loop(quote_hub), name="quote-push-ping"),
        ]
        logger.info("Quote push hub started (poll=%ss)",
                    settings.MARKET_PUSH_POLL_SECONDS)

    logger.info("Database engine and Redis pool ready")
    yield  # Application runs here ──────────────────────────────────────

    # Teardown — cancel the heartbeat BEFORE closing the pool it uses.
    if live_stop is not None:
        live_stop.set()
        for t in live_tasks:
            t.cancel()
        for t in live_tasks:
            with suppress(asyncio.CancelledError):
                await t
    if live_mgr is not None:
        await live_mgr.shutdown()
        logger.info("Live trading manager stopped (runners killed)")
    if paper_mgr is not None:
        await paper_mgr.shutdown()
        logger.info("Paper trading manager stopped (runners killed)")
    if bt_queue is not None:
        await bt_queue.shutdown()
        logger.info("Backtest queue stopped (all runner subprocesses killed)")
    if tick_queue is not None:
        await tick_queue.shutdown()
        logger.info("Tick sync queue stopped")
    if heartbeat_task is not None:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
        logger.info("Gateway heartbeat loop stopped")
    if rules_task is not None:
        rules_task.cancel()
        with suppress(asyncio.CancelledError):
            await rules_task
        logger.info("Alert-rule evaluation loop stopped")
    if patrol_task is not None:
        patrol_task.cancel()
        with suppress(asyncio.CancelledError):
            await patrol_task
        logger.info("Audit-chain patrol loop stopped")
    if history_task is not None:
        history_task.cancel()
        with suppress(asyncio.CancelledError):
            await history_task
        logger.info("Market history sync loop stopped")
    if nb_sweep_task is not None:
        nb_sweep_task.cancel()
        with suppress(asyncio.CancelledError):
            await nb_sweep_task
        logger.info("Notebook session sweep loop stopped")
    for t in quote_tasks:
        t.cancel()
    for t in quote_tasks:
        with suppress(asyncio.CancelledError):
            await t
    if quote_tasks:
        await shutdown_quote_hub()
        logger.info("Quote push hub stopped")
    logger.info("Shutting down …")
    await close_redis()
    await close_db()
    logger.info("All connections closed")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title=settings.APP_TITLE,
        version=settings.APP_VERSION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        debug=settings.DEBUG,
    )

    # -- CORS ----------------------------------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.CORS_ORIGINS),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Routers -------------------------------------------------------------
    app.include_router(v1_router, prefix="/api/v1")
    # WS 行情流（app 级路由：vite 代理仅在 /ws 前缀升级 WebSocket）
    app.include_router(market_ws_router)

    # -- Health check (outside versioned API) --------------------------------
    @app.get("/health", tags=["health"])
    async def health_check():
        """Lightweight liveness probe — returns 200 when the process is up."""
        return JSONResponse(
            content={
                "status": "healthy",
                "version": settings.APP_VERSION,
            },
        )

    return app


# ---------------------------------------------------------------------------
# Module-level singleton (used by ``uvicorn app.main:app``)
# ---------------------------------------------------------------------------
app: FastAPI = create_app()
