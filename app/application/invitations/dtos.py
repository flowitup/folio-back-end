"""Data Transfer Objects for invitation use-case results.

All DTOs are frozen dataclasses so callers cannot mutate them after creation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from app.domain.entities.invitation import InvitationStatus
from app.domain.entities.user import User


@dataclass(frozen=True)
class CreateInvitationResultDto:
    """Result of CreateInvitationUseCase.execute().

    kind='invitation_sent' — new invitation was created and email queued.
    kind='direct_added'   — email matched an existing user; membership added directly.
    """

    kind: Literal["invitation_sent", "direct_added"]
    invitation_id: Optional[UUID] = None
    expires_at: Optional[datetime] = None
    user_id: Optional[UUID] = None


@dataclass(frozen=True)
class VerifyInvitationDto:
    """Safe metadata returned to the accept-invite landing page (no secrets)."""

    email: str
    project_name: str
    role_name: str
    inviter_name: str
    expires_at: datetime


@dataclass(frozen=True)
class AcceptInvitationResultDto:
    """Result of AcceptInvitationUseCase — used by the API layer to set cookies."""

    user: User
    access_token: str
    refresh_token: str


@dataclass(frozen=True)
class InvitationListItemDto:
    """Single row in the project invitations list."""

    id: UUID
    email: str
    role_name: str
    status: InvitationStatus
    expires_at: datetime
    created_at: datetime
    invited_by_name: str
