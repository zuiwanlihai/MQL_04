"""
Low-level security primitives: password hashing and JWT issuance.

Password hashes are Argon2id (argon2-cffi) — matching the intent of the
``User.password_hash`` comment.  Access tokens are short-lived HS256 JWTs;
refresh tokens are opaque random strings stored only as SHA-256 digests.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from jose import JWTError, jwt

from app.config import settings

# Single shared hasher — Argon2id parameters are constant for all users.
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Constant-time verification; never raises on bad input."""
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash uses outdated parameters."""
    return _hasher.check_needs_rehash(password_hash)


def create_access_token(user_id: uuid.UUID, session_id: uuid.UUID) -> str:
    """Issue a short-lived access token bound to a session."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "type": "access",
        "iat": now,
        "exp": expire,
        "iss": settings.TOTP_ISSUER,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def generate_refresh_token() -> str:
    """Opaque, URL-safe refresh token (raw value never persisted)."""
    return secrets.token_urlsafe(64)


def create_pre_auth_token(user_id: uuid.UUID) -> str:
    """Short-lived JWT for the 2FA step between password and TOTP verification.

    Carries ``purpose="2fa_pending"`` and **no sid**, so it can never be used
    against business endpoints — only ``POST /auth/login/2fa`` accepts it.
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(seconds=settings.TOTP_PREAUTH_TTL_SECONDS)
    payload = {
        "sub": str(user_id),
        "purpose": "2fa_pending",
        "iat": now,
        "exp": expire,
        "iss": settings.TOTP_ISSUER,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_pre_auth_token(token: str) -> uuid.UUID:
    """Validate a pre-auth token; returns the user id.

    Raises :class:`jose.JWTError` on any invalid/expired/wrong-purpose token
    (callers map that to 401).
    """
    payload = jwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
    )
    if payload.get("purpose") != "2fa_pending":
        raise JWTError("not a 2fa pre-auth token")
    return uuid.UUID(payload["sub"])


def sha256_hex(token: str) -> str:
    """SHA-256 hex digest used as the persisted refresh-token identifier."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
