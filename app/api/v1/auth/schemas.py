"""Pydantic schemas for auth endpoints."""

from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Login request payload."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class OtpRequestBody(BaseModel):
    """POST /auth/otp/request — ask for a 6-digit code by SMS."""

    phone: str = Field(..., min_length=6, max_length=32)


class OtpRequestResponse(BaseModel):
    """Always 202: the code (if the phone is known) is valid for ``expires_in`` seconds."""

    expires_in: int


class OtpVerifyBody(BaseModel):
    """POST /auth/otp/verify — exchange phone + code for tokens."""

    phone: str = Field(..., min_length=6, max_length=32)
    code: str = Field(..., pattern=r"^\s*\d{6}\s*$")


class AuthConfigResponse(BaseModel):
    """GET /auth/config — what this deployment offers, read by the apps before sign-in."""

    login_mode: str  # "email" | "phone" | "both"
    session: str  # "expiring" (7-day refresh token) | "persistent" (until sign-out)
    signup: bool  # phone self-registration offered (LOGIN_MODE phone/both)


class SignupRequestBody(BaseModel):
    """POST /auth/signup/request — text a sign-up code to a phone without an account."""

    phone: str = Field(..., min_length=6, max_length=32)


class SignupVerifyBody(BaseModel):
    """POST /auth/signup/verify — create the account and sign in."""

    phone: str = Field(..., min_length=6, max_length=32)
    code: str = Field(..., pattern=r"^\s*\d{6}\s*$")
    display_name: str = Field(..., min_length=1, max_length=80)


class LogoutBody(BaseModel):
    """Optional body of POST /auth/logout: Bearer clients pass their refresh token so it is revoked too."""

    refresh_token: Optional[str] = None


class UserResponse(BaseModel):
    """User info response."""

    id: UUID
    email: str
    permissions: List[str]
    roles: List[str]
    phone: Optional[str] = None


class LoginResponse(BaseModel):
    """Login response with tokens and user info."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = 1800  # 30 minutes in seconds
    user: UserResponse


class RefreshResponse(BaseModel):
    """Refresh token response."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 1800


class LogoutResponse(BaseModel):
    """Logout response."""

    message: str = "Successfully logged out"


class ErrorResponse(BaseModel):
    """Error response."""

    error: str
    message: str
    status_code: int
