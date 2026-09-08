"""ListInvitationsUseCase — return paginated invitation list for a project."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from app.application.invitations.authz import can_manage_project_invites
from app.application.invitations.dtos import InvitationListItemDto
from app.application.invitations.exceptions import PermissionDeniedError
from app.application.invitations.ports import (
    InvitationRepositoryPort,
    UserWriteRepositoryPort,
)
from app.domain.companies.roles import CompanyRole
from app.domain.entities.invitation import InvitationStatus


class ListInvitationsUseCase:
    """List invitations for a project, optionally filtered by status."""

    def __init__(
        self,
        invitation_repo: InvitationRepositoryPort,
        user_repo: UserWriteRepositoryPort,
        authz_reader: Any = None,  # AuthzReaderPort — resolves project:invite
    ) -> None:
        self._inv_repo = invitation_repo
        self._user_repo = user_repo
        self._authz_reader = authz_reader

    def set_authz_reader(self, reader: Any) -> None:
        """Inject the resolver read port after construction (see the revoke use-case)."""
        self._authz_reader = reader

    # ------------------------------------------------------------------

    def execute(
        self,
        requester_id: UUID,
        project_id: UUID,
        status_filter: str = "pending",
    ) -> list[InvitationListItemDto]:
        """Return invitations for *project_id*, filtered by *status_filter*.

        Raises:
            PermissionDeniedError: requester may not manage this project's invitations.
        """
        requester = self._user_repo.find_by_id(requester_id)
        if requester is None:
            raise PermissionDeniedError(f"User {requester_id} not found.")

        # Pending invitations are part of managing a project's people: the same
        # `project:invite` that creating and revoking one requires, resolved on
        # THIS project (never a legacy global role, never a bare membership row).
        if not can_manage_project_invites(self._authz_reader, requester_id, project_id):
            raise PermissionDeniedError(f"User {requester_id} cannot manage invitations of project {project_id}.")

        status_enum: Optional[InvitationStatus] = None
        if status_filter:
            try:
                status_enum = InvitationStatus(status_filter.lower())
            except ValueError:
                # Unknown status value — return empty list rather than crashing
                return []

        invitations = self._inv_repo.list_by_project(project_id, status=status_enum)

        result: list[InvitationListItemDto] = []
        for inv in invitations:
            inviter = self._user_repo.find_by_id(inv.invited_by)
            inviter_name = inviter.display_or_email if inviter else str(inv.invited_by)

            result.append(
                InvitationListItemDto(
                    id=inv.id,
                    email=inv.email,
                    # Every pending invitation grants the same thing: company
                    # `member` plus an assignment to the invited project.
                    role_name=CompanyRole.MEMBER.value,
                    status=inv.status,
                    expires_at=inv.expires_at,
                    created_at=inv.created_at,
                    invited_by_name=inviter_name,
                )
            )

        return result
