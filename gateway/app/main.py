"""MT5 gateway service — FastAPI entry-point.

Run (single worker, NO --reload — reload/multi-worker breaks the single
terminal connection)::

    .venv\\Scripts\\python -m uvicorn app.main:app --host 127.0.0.1 --port 8090

Ops (M11): a state file marks this run; a stale file at boot means the
previous process died uncleanly → ``/health`` reports ``abnormal_restart``
and the backend raises the「请先核对持仓」alert.  A background terminal-guard
loop relaunches the MT5 terminal when it disappears (capped retries).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import ops, security
from app.config import settings
from app.mt5_client import MT5Error, client
from app.routes import account, health, market, selfcheck, terminal, trade

logger = logging.getLogger("gateway")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # 第一件事就是验密钥：没配好的网关不该先连终端、再开始对外服务。
    # 未配置时在这里抛 RuntimeError → uvicorn 报 "Application startup failed"
    # 并以非零码退出，而不是留下一个无鉴权的交易端口。
    security.assert_key_configured()

    logger.info("MT5 gateway starting on %s:%s", settings.GATEWAY_HOST, settings.GATEWAY_PORT)

    # -- abnormal-restart detection (state file from the previous run) --
    ops.ABNORMAL_RESTART = ops.write_state_file()
    if ops.ABNORMAL_RESTART is not None:
        logger.error("previous gateway run did NOT exit cleanly (state file %s) — "
                     "abnormal_restart flag raised for the backend alert",
                     ops.ABNORMAL_RESTART)

    # -- terminal guard loop (M11) --
    guard = ops.TerminalGuard(client)
    guard_task = asyncio.create_task(ops.guard_loop(guard), name="terminal-guard")

    try:
        if settings.MT5_LOGIN and settings.MT5_PASSWORD and settings.MT5_SERVER:
            try:
                summary = await client.login(
                    settings.MT5_LOGIN, settings.MT5_PASSWORD, settings.MT5_SERVER
                )
                logger.info(
                    "Auto-login OK: account %s @ %s",
                    summary["login"], summary["server"],
                )
            except MT5Error as exc:
                logger.warning("Auto-login failed: %s", exc.detail)
        yield
    finally:
        # Cancel the guard loop BEFORE shutdown — shutdown clears the terminal
        # handle, so any guard tick after that would see reachable=False and
        # try to spawn a real terminal (which in CI/test environments asserts).
        # try/finally guarantees cleanup even if lifespan raises *before* yield
        # (e.g. assert_key_configured fails), leaving no orphan guard_task.
        guard_task.cancel()
        try:
            await guard_task
        except asyncio.CancelledError:
            pass
        try:
            await client.shutdown()
        finally:
            # Runs even if the release above is interrupted (CancelledError is a
            # BaseException, so MT5Client.shutdown's own guard does not cover it).
            # A state file left behind here would raise a spurious
            # 「请先核对持仓」alert on the next boot.
            ops.clear_state_file()  # clean exit → next boot is not "abnormal"
        logger.info("MT5 gateway stopped")


def create_app() -> FastAPI:
    app = FastAPI(title="MT5 Gateway", version="1.0.0", lifespan=lifespan)

    @app.exception_handler(MT5Error)
    async def _mt5_error(request: Request, exc: MT5Error) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    app.include_router(health.router)
    app.include_router(selfcheck.router)
    app.include_router(account.router)
    app.include_router(market.router)
    app.include_router(trade.router)
    app.include_router(terminal.router)
    return app


app = create_app()
