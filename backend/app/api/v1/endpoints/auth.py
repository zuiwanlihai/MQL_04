"""
Authentication endpoints: register / login (2FA-aware) / refresh / logout / me
+ TOTP enrollment (M9 双因素认证).

Login is a two-step flow only for accounts with 2FA enabled; accounts
without 2FA take the exact pre-M9 single-step path (invariant).
Every login/logout outcome is audited (PRD M7 ①「登录/登出（含 2FA 结果）」).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from jose import JWTError

from app.dependencies import CurrentUser, DBSession
from app.schemas.auth import (
    LoginTwoFARequest,
    LogoutRequest,
    RefreshRequest,
    SessionResponse,
    TwoFAChallenge,
    TwoFADisableRequest,
    TwoFAEnableOut,
    TwoFAEnableRequest,
    TwoFASetupOut,
    TwoFAStatusOut,
    UserLogin,
    UserRegister,
    UserResponse,
)
from app.services import auth as auth_service
from app.services.audit import AuditActor
from app.services.audit import record as audit_record
from app.services.security import decode_pre_auth_token

router = APIRouter()


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    """Best-effort client IP + User-Agent for the session record."""
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ua, ip


def _actor_for(request: Request, email: str = "", user_id: uuid.UUID | None = None) -> AuditActor:
    ip = request.client.host if request.client else ""
    return AuditActor(actor_type="user", actor_id=user_id,
                      actor_label=(email or str(user_id or ""))[:120],
                      source_ip=ip or "")


@router.post(
    "/register", response_model=SessionResponse, status_code=status.HTTP_201_CREATED
)
async def register(payload: UserRegister, request: Request, db: DBSession) -> SessionResponse:
    """Create a new account and start a session (register = login)."""
    ua, ip = _client_meta(request)
    session = await auth_service.register_user(db, payload, ua, ip)
    await audit_record("login", actor=_actor_for(request, payload.email, session.user.id),
                       target_type="user", target_id=str(session.user.id),
                       params={"via": "register"})
    return session


@router.post("/login", response_model=SessionResponse | TwoFAChallenge)
async def login(payload: UserLogin, request: Request, db: DBSession):
    """Verify credentials; issue a session, or a 2FA challenge when enabled."""
    ua, ip = _client_meta(request)
    try:
        result = await auth_service.authenticate_user(db, payload, ua, ip)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            await audit_record("login", actor=_actor_for(request, payload.email),
                               target_type="user", target_id="",
                               params={"email": payload.email},
                               result="failure", error_code="BAD_CREDENTIALS")
        raise
    if isinstance(result, TwoFAChallenge):
        await audit_record("login", actor=_actor_for(request, payload.email),
                           target_type="user", target_id="",
                           params={"via": "password", "outcome": "2fa_challenge"})
        return result
    await audit_record("login", actor=_actor_for(request, payload.email, result.user.id),
                       target_type="user", target_id=str(result.user.id),
                       params={"via": "password", "outcome": "session"})
    return result


@router.post("/login/2fa", response_model=SessionResponse)
async def login_2fa(payload: LoginTwoFARequest, request: Request, db: DBSession) -> SessionResponse:
    """Second login step for 2FA accounts: TOTP or recovery code."""
    ua, ip = _client_meta(request)
    try:
        user_id = decode_pre_auth_token(payload.pre_auth_token)
    except (JWTError, ValueError, KeyError):
        raise HTTPException(status_code=401, detail="预授权令牌无效或已过期，请重新登录") from None
    try:
        session, via = await auth_service.complete_2fa_login(db, user_id, payload.totp_code, ua, ip)
    except HTTPException as exc:
        if exc.status_code == 401:
            await audit_record("login_2fa", actor=_actor_for(request, user_id=user_id),
                               target_type="user", target_id=str(user_id),
                               result="failure", error_code="BAD_TOTP")
        raise
    await audit_record("login_2fa", actor=_actor_for(request, session.user.email, user_id),
                       target_type="user", target_id=str(user_id),
                       params={"via": via})
    return session


@router.get("/2fa/status", response_model=TwoFAStatusOut)
async def two_fa_status(current: CurrentUser, db: DBSession) -> TwoFAStatusOut:
    return await auth_service.get_2fa_status(db, uuid.UUID(current["sub"]))


@router.post("/2fa/setup", response_model=TwoFASetupOut)
async def two_fa_setup(
    current: CurrentUser, db: DBSession, request: Request
) -> TwoFASetupOut:
    out = await auth_service.setup_2fa(db, uuid.UUID(current["sub"]))
    await audit_record("2fa_setup", actor=_actor_for(request, str(current.get("email") or ""),
                                                     uuid.UUID(current["sub"])),
                       target_type="user", target_id=current["sub"])
    return out


@router.post("/2fa/enable", response_model=TwoFAEnableOut)
async def two_fa_enable(
    payload: TwoFAEnableRequest, current: CurrentUser, db: DBSession, request: Request
) -> TwoFAEnableOut:
    out = await auth_service.enable_2fa(db, uuid.UUID(current["sub"]), payload.code)
    await audit_record("2fa_enable", actor=_actor_for(request, str(current.get("email") or ""),
                                                      uuid.UUID(current["sub"])),
                       target_type="user", target_id=current["sub"],
                       params={"recovery_codes_issued": len(out.recovery_codes)})
    return out


@router.post("/2fa/disable", status_code=status.HTTP_204_NO_CONTENT)
async def two_fa_disable(
    payload: TwoFADisableRequest, current: CurrentUser, db: DBSession, request: Request
) -> None:
    await auth_service.disable_2fa(db, uuid.UUID(current["sub"]), payload.password)
    await audit_record("2fa_disable", actor=_actor_for(request, str(current.get("email") or ""),
                                                       uuid.UUID(current["sub"])),
                       target_type="user", target_id=current["sub"])


@router.post("/refresh", response_model=SessionResponse)
async def refresh(payload: RefreshRequest, db: DBSession) -> SessionResponse:
    """Rotate the refresh token and return a new token pair."""
    return await auth_service.refresh_session(db, payload.refresh_token)


@router.post("/logout")
async def logout(payload: LogoutRequest, db: DBSession, request: Request) -> dict:
    """Revoke the session identified by the refresh token (idempotent)."""
    await auth_service.logout_session(db, payload.refresh_token)
    await audit_record("logout", actor=_actor_for(request),
                       target_type="user", target_id="")
    return {"message": "logged out"}


@router.get("/me", response_model=UserResponse)
async def me(current: CurrentUser, db: DBSession) -> UserResponse:
    """Return the authenticated user's profile."""
    user = await auth_service.get_user_for_me(db, current["sub"])
    return UserResponse.model_validate(user)
