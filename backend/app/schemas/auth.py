"""
Auth request/response schemas.

Field names mirror ``frontend/src/types/api.ts`` — that file is the
contract; keep the two in sync.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class UserRegister(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.]+$")
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


# ---------------------------------------------------------------------------
# Two-factor authentication (M9)
# ---------------------------------------------------------------------------
class TwoFAChallenge(BaseModel):
    """Returned by /auth/login when the account has 2FA enabled."""

    requires_2fa: bool = True
    pre_auth_token: str = Field(description="5 分钟有效的一次性预授权令牌（非访问令牌）")
    ttl_seconds: int


class LoginTwoFARequest(BaseModel):
    pre_auth_token: str = Field(min_length=1)
    totp_code: str = Field(min_length=6, max_length=16,
                           description="验证器 6 位动态码，或 8 位恢复码（XXXX-XXXX）")


class TwoFASetupOut(BaseModel):
    secret: str = Field(description="Base32 明文密钥，仅本次返回，用于手动录入")
    otpauth_uri: str
    qr_svg_base64: str = Field(description="data:image/svg+xml;base64,… 二维码")


class TwoFAEnableRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8, description="验证器当前 6 位码")


class TwoFAEnableOut(BaseModel):
    recovery_codes: list[str] = Field(description="一次性恢复码，请立即保存，每个仅可用一次")


class TwoFADisableRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128, description="重确认账号密码")


class TwoFAStatusOut(BaseModel):
    enabled: bool
    recovery_codes_remaining: int


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------
class UserResponse(BaseModel):
    """Mirrors the frontend ``User`` type."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    username: str
    full_name: str
    role: Literal["admin", "trader", "analyst", "viewer"]
    is_active: bool
    totp_enabled: bool = False
    created_at: datetime
    updated_at: datetime | None = None


class SessionResponse(BaseModel):
    """Mirrors the frontend ``Session`` type."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse
