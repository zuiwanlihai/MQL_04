"""
SQLAlchemy async engine and session factory.

Uses the project's :class:`Settings` for connection parameters so everything
stays in one place.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the module-level async engine, creating it on first call."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.DATABASE_URL,
            pool_size=settings.DATABASE_POOL_SIZE,
            max_overflow=settings.DATABASE_MAX_OVERFLOW,
            pool_recycle=settings.DATABASE_POOL_RECYCLE,
            echo=settings.DEBUG,
            future=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the module-level session factory, creating it on first call."""
    global _session_factory
    if _session_factory is None:
        engine = get_engine()
        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


# Convenience alias for use in ``dependencies.py``
AsyncSessionLocal: async_sessionmaker[AsyncSession] = (
    get_session_factory()  # bootstrap eagerly in simple deployments
)


async def init_db() -> None:
    """Ensure the engine and session factory are ready.

    Called by the application startup handler.  Safe to call multiple times.
    """
    get_engine()
    get_session_factory()


async def close_db() -> None:
    """Dispose of the engine's connection pool.  Called at shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    _session_factory = None
