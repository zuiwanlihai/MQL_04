"""
Auth business logic: registration, login, refresh rotation, logout.

All functions take an open ``AsyncSession`` and commit within the
service — endpoints stay thin.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.models.user_session import UserSession
from app.schemas.auth import (
    SessionResponse,
    TwoFAChallenge,
    TwoFAEnableOut,
    TwoFASetupOut,
    TwoFAStatusOut,
    UserLogin,
    UserRegister,
    UserResponse,
)
from app.services import totp
from app.services.credentials import decrypt_secret, encrypt_secret
from app.services.security import (
    create_access_token,
    create_pre_auth_token,
    generate_refresh_token,
    hash_password,
    needs_rehash,
    sha256_hex,
    verify_password,
)


async def _get_user_by_email(db: AsyncSession, email: str) -> User | None:
    return (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def _get_user_by_username(db: AsyncSession, username: str) -> User | None:
    return (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()


async def _get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


async def _issue_session(
    db: AsyncSession,
    user: User,
    user_agent: str | None,
    ip_address: str | None,
) -> SessionResponse:
    """Create a session row and issue the token pair."""
    raw_refresh = generate_refresh_token()
    now = datetime.now(timezone.utc)
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=sha256_hex(raw_refresh),
        user_agent=user_agent[:255] if user_agent else None,
        ip_address=ip_address,
        expires_at=now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        last_used_at=now,
    )
    db.add(session)
    await db.flush()  # populate session.id for the access-token "sid" claim
    access_token = create_access_token(user.id, session.id)
    await db.commit()
    return SessionResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse.model_validate(user),
    )


async def register_user(
    db: AsyncSession,
    payload: UserRegister,
    user_agent: str | None,
    ip_address: str | None,
) -> SessionResponse:
    """Create a new user (first user becomes admin) and log them in."""
    if await _get_user_by_email(db, payload.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    if await _get_user_by_username(db, payload.username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Username already taken"
        )

    # Bootstrap: the very first user is the platform admin.
    user_count = (await db.execute(select(func.count(User.id)))).scalar_one()
    is_first = user_count == 0

    user = User(
        email=payload.email,
        username=payload.username,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role="admin" if is_first else "trader",
        is_superuser=is_first,
    )
    db.add(user)
    await db.flush()
    return await _issue_session(db, user, user_agent, ip_address)


async def authenticate_user(
    db: AsyncSession,
    payload: UserLogin,
    user_agent: str | None,
    ip_address: str | None,
) -> SessionResponse | TwoFAChallenge:
    """Verify credentials; issue a session, or a 2FA challenge when enabled.

    Invariant: accounts with ``totp_enabled=false`` take the exact pre-M9
    path (session row + token pair).  2FA accounts get a short-lived
    pre-auth token and **no** UserSession row until the code step completes.
    """
    user = await _get_user_by_email(db, payload.email)
    # Uniform 401 — do not reveal whether the email exists.
    # (English detail: the frontend DETAIL_TRANSLATIONS map renders Chinese.)
    if user is None or not verify_password(user.password_hash, payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account disabled")

    if user.totp_enabled:
        return TwoFAChallenge(
            pre_auth_token=create_pre_auth_token(user.id),
            ttl_seconds=settings.TOTP_PREAUTH_TTL_SECONDS,
        )

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
    user.last_login_at = datetime.now(timezone.utc)
    await db.flush()
    return await _issue_session(db, user, user_agent, ip_address)


async def complete_2fa_login(
    db: AsyncSession,
    user_id: uuid.UUID,
    totp_code: str,
    user_agent: str | None,
    ip_address: str | None,
) -> tuple[SessionResponse, str]:
    """Second login step: verify the TOTP / recovery code, then issue session.

    Returns ``(session, via)`` where ``via`` is ``totp`` or ``recovery`` (for
    the audit trail).  A matching recovery code is consumed (single use).
    Wrong/expired codes return the uniform 401 (no enumeration, no partial
    state).
    """
    user = await _get_user_by_id(db, user_id)
    if user is None or not user.is_active or not user.totp_enabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="验证码错误或已过期")

    code = totp_code.strip().upper()
    via = "totp"
    ok = False
    if user.totp_secret:
        try:
            ok = totp.verify_code(decrypt_secret(user.totp_secret), code)
        except Exception:  # decryption failure → treat as invalid, never 500
            ok = False

    if not ok and "-" in code:
        idx = totp.match_recovery_code(list(user.recovery_codes or []), code)
        if idx >= 0:
            remaining = list(user.recovery_codes)
            remaining.pop(idx)
            user.recovery_codes = remaining
            via = "recovery"
            ok = True

    if not ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="验证码错误或已过期")

    user.last_login_at = datetime.now(timezone.utc)
    await db.flush()
    session = await _issue_session(db, user, user_agent, ip_address)
    return session, via


async def setup_2fa(db: AsyncSession, user_id: uuid.UUID) -> TwoFASetupOut:
    """Generate + stash (still disabled) a fresh secret; return provisioning data.

    Plaintext secret is returned exactly once for QR/manual entry; the stored
    column holds Fernet ciphertext.  Calling this again rotates the secret.
    """
    user = await _get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    if user.totp_enabled:
        raise HTTPException(status_code=409, detail="两步验证已启用，请先停用后重新设置")

    secret = totp.new_secret()
    user.totp_secret = encrypt_secret(secret)
    user.recovery_codes = []
    await db.commit()
    uri = totp.provisioning_uri(user.email, secret, settings.TOTP_ISSUER)
    return TwoFASetupOut(
        secret=secret,
        otpauth_uri=uri,
        qr_svg_base64=totp.qr_svg_data_uri(uri),
    )


async def enable_2fa(db: AsyncSession, user_id: uuid.UUID, code: str) -> TwoFAEnableOut:
    """Confirm the setup code, flip totp_enabled and mint recovery codes."""
    user = await _get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    if user.totp_enabled:
        raise HTTPException(status_code=409, detail="两步验证已启用")
    if not user.totp_secret:
        raise HTTPException(status_code=409, detail="请先发起设置获取二维码")

    try:
        secret = decrypt_secret(user.totp_secret)
    except Exception:
        raise HTTPException(status_code=409, detail="密钥状态异常，请重新发起设置") from None
    if not totp.verify_code(secret, code):
        raise HTTPException(status_code=422, detail="验证码不正确，请确认验证器时间与所选账户")

    codes = totp.gen_recovery_codes()
    user.totp_enabled = True
    user.recovery_codes = totp.hash_recovery_codes(codes)
    await db.commit()
    return TwoFAEnableOut(recovery_codes=codes)


async def disable_2fa(db: AsyncSession, user_id: uuid.UUID, password: str) -> None:
    """Password re-confirmation, then wipe secret + recovery codes."""
    user = await _get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    if not user.totp_enabled:
        raise HTTPException(status_code=409, detail="两步验证未启用")
    if not verify_password(user.password_hash, password):
        raise HTTPException(status_code=401, detail="密码错误")

    user.totp_enabled = False
    user.totp_secret = None
    user.recovery_codes = []
    await db.commit()


async def get_2fa_status(db: AsyncSession, user_id: uuid.UUID) -> TwoFAStatusOut:
    user = await _get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    return TwoFAStatusOut(
        enabled=user.totp_enabled,
        recovery_codes_remaining=len(user.recovery_codes or []),
    )


async def refresh_session(
    db: AsyncSession, refresh_token: str
) -> SessionResponse:
    """Rotate the refresh token and issue a fresh access token."""
    token_hash = sha256_hex(refresh_token)
    row = (
        await db.execute(
            select(UserSession).where(UserSession.refresh_token_hash == token_hash)
        )
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session"
        )

    user = await _get_user_by_id(db, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account disabled")

    # Rotate: new raw token, new hash, extended expiry.
    raw_refresh = generate_refresh_token()
    row.refresh_token_hash = sha256_hex(raw_refresh)
    row.expires_at = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    row.last_used_at = now
    await db.flush()

    access_token = create_access_token(user.id, row.id)
    await db.commit()
    return SessionResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse.model_validate(user),
    )


async def logout_session(db: AsyncSession, refresh_token: str) -> None:
    """Revoke the session. Idempotent — unknown tokens are a no-op."""
    token_hash = sha256_hex(refresh_token)
    row = (
        await db.execute(
            select(UserSession).where(UserSession.refresh_token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        await db.commit()


async def get_user_for_me(db: AsyncSession, user_id: str) -> User:
    """Load the current user for ``GET /auth/me``."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials"
        ) from None
    user = await _get_user_by_id(db, uid)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials"
        )
    return user
