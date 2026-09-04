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
