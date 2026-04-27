"""Pydantic schemas for invitation endpoints."""

from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class CreateInviteRequest(BaseModel):
    """POST /invitations request body."""

    project_id: UUID
    email: EmailStr
    role_id: UUID


class CreateInviteResponse(BaseModel):
    """POST /invitations response body.

    SECURITY/UX NOTE — kind discriminator leak (H3 from code-review):
    ``kind`` reveals whether the supplied email belongs to an existing user
    (``direct_added``) or not (``invitation_sent``). Accepted within the admin
    trust boundary because ``project:invite`` callers can already enumerate
    users via /projects/<id>/members. Do NOT echo this discriminator on any
    public-facing endpoint.
    """

    kind: Literal["invitation_sent", "direct_added"]
    invitation_id: Optional[UUID] = None
    expires_at: Optional[datetime] = None
    user_id: Optional[UUID] = None  # set when kind='direct_added'


class VerifyInviteResponse(BaseModel):
    """GET /invitations/verify/<token> response body."""

    email: EmailStr
    project_name: str
    role_name: str
    inviter_name: str
    expires_at: datetime


class AcceptInviteRequest(BaseModel):
    """POST /invitations/accept request body."""

    token: str = Field(min_length=10, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)


class InvitationListItem(BaseModel):
    """Single row in the project invitations list."""

    id: UUID
    email: EmailStr
    role_name: str
    status: Literal["pending", "accepted", "revoked", "expired"]
    expires_at: datetime
    created_at: datetime
    invited_by_name: str


class InvitationListResponse(BaseModel):
    """GET /projects/<id>/invitations response body."""

    items: List[InvitationListItem]


class AcceptedUserResponse(BaseModel):
    """User info returned on successful accept."""

    id: UUID
    email: str
    display_name: Optional[str] = None
