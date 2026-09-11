"""Pydantic schemas for auth endpoints."""

from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


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

    session: str  # "expiring" (7-day refresh token) | "persistent" (until sign-out)
    signup: bool  # phone self-registration — always true; phone is the only sign-in method


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


class UserCompanySummary(BaseModel):
    """One company the user is attached to — company role, not global RBAC."""

    id: UUID
    legal_name: str
    role: str  # "admin" | "manager" | "member" — see app.domain.companies.roles.CompanyRole
    is_primary: bool


class UserResponse(BaseModel):
    """User info response.

    `permissions` is resolved from the caller's company role (primary company)
    for client-side UI gating only — the server re-resolves on every route.
    """

    id: UUID
    email: str
    # Name the user chose at sign-up; clients show it instead of the e-mail, which for
    # phone sign-ups is a synthetic `phone-<number>@no-email...` address.
    display_name: Optional[str] = None
    permissions: List[str]
    phone: Optional[str] = None
    companies: List[UserCompanySummary] = []
    is_platform_ops: bool = False


class UpdateMeRequest(BaseModel):
    """PATCH /auth/me — the caller edits their own display name and/or phone.

    The e-mail is deliberately not editable here (platform ops only). ``phone`` is stored in
    E.164 and must stay unique; null/empty clears it. At least one field must be provided.
    """

    display_name: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=32)


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
