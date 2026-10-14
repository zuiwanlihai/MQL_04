"""
FastAPI dependency callables.

Every dependency is a thin wrapper around a resource manager (database session,
Redis connection, JWT token verification) so that route handlers can request
them with ``Depends(get_db)`` and friends.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.engine.redis import get_redis_client
from app.engine.session import AsyncSessionLocal

# ---------------------------------------------------------------------------
# Security scheme shared by all authenticated endpoints
# ---------------------------------------------------------------------------
_bearer_scheme = HTTPBearer(
    scheme_name="JWT",
    description="Enter a valid JWT access token.",
    auto_error=True,
)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async SQLAlchemy session bound to the application's engine.

    The session is automatically closed (returned to the pool) when the
    request finishes, even if an unhandled exception occurs.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------
async def get_redis() -> aioredis.Redis:
    """Return a connected async Redis client.

    The client itself is obtained from a connection-pool factory; callers
    **must not** close it as the pool manages lifecycle.
    """
    return await get_redis_client()


# ---------------------------------------------------------------------------
# Authentication / Authorisation
# ---------------------------------------------------------------------------
async def get_current_user(
    token: Annotated[HTTPAuthorizationCredentials, Depends(_bearer_scheme)],
) -> dict:
    """Decode the JWT from the ``Authorization: Bearer <token>`` header.

    Returns
    -------
    dict
        The token payload (sub, exp, …).  The ``sub`` claim is the user-id.

    Raises
    ------
    HTTPException 401
        If the token is missing, expired, or otherwise invalid.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload: dict = jwt.decode(
            token.credentials,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError:
        raise credentials_exception from None

    sub: str | None = payload.get("sub")
    if sub is None:
        raise credentials_exception

    # M9: 2FA pre-auth tokens are NOT access tokens — reject them here even
    # though the signature/expiry are valid (they lack sid/type=access).
    if payload.get("purpose") == "2fa_pending":
        raise credentials_exception

    # Optional: verify the user still exists in the DB / is not deactivated.
    # This is deliberately left for the service layer so that the dependency
    # stays lightweight.

    return payload


# ---------------------------------------------------------------------------
# Convenience type aliases (used by FastAPI's ``Annotated`` injection)
# ---------------------------------------------------------------------------
DBSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[dict, Depends(get_current_user)]
RedisClient = Annotated[aioredis.Redis, Depends(get_redis)]
