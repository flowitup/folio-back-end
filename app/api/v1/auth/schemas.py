"""Pydantic schemas for auth endpoints."""

from typing import List
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Login request payload."""
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class UserResponse(BaseModel):
    """User info response."""
    id: UUID
    email: str
    permissions: List[str]
    roles: List[str]


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
