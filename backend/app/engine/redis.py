"""
Async Redis connection-pool manager.

Provides a single :func:`get_redis_client` callable that returns a connected
``redis.asyncio.Redis`` instance, backed by a lazily-created connection pool.
"""

from __future__ import annotations

import redis.asyncio as aioredis

from app.config import settings

_pool: aioredis.ConnectionPool | None = None


async def init_redis() -> None:
    """Initialise (or re-initialise) the Redis connection pool.

    Safe to call at application startup and after `close_redis`.
    """
    global _pool
    if _pool is not None:
        await _pool.disconnect(inuse_connections=True)
    _pool = aioredis.ConnectionPool.from_url(
        settings.REDIS_URL,
        max_connections=settings.REDIS_MAX_CONNECTIONS,
        decode_responses=True,
    )


async def get_redis_client() -> aioredis.Redis:
    """Return an async Redis client wired to the shared connection pool."""
    global _pool
    if _pool is None:
        await init_redis()
    return aioredis.Redis(connection_pool=_pool)  # type: ignore[arg-type]


async def close_redis() -> None:
    """Tear down the Redis connection pool.  Called at application shutdown."""
    global _pool
    if _pool is not None:
        await _pool.disconnect(inuse_connections=True)
        _pool = None
