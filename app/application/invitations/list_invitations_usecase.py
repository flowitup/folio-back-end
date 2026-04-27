"""ListInvitationsUseCase — return paginated invitation list for a project."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.application.invitations.dtos import InvitationListItemDto
from app.application.invitations.exceptions import PermissionDeniedError
from app.application.invitations.ports import (
    InvitationRepositoryPort,
    ProjectMembershipRepositoryPort,
    RoleRepositoryPort,
    UserWriteRepositoryPort,
)
from app.domain.entities.invitation import InvitationStatus


class ListInvitationsUseCase:
    """List invitations for a project, optionally filtered by status."""

    def __init__(
        self,
        invitation_repo: InvitationRepositoryPort,
        project_membership_repo: ProjectMembershipRepositoryPort,
        role_repo: RoleRepositoryPort,
        user_repo: UserWriteRepositoryPort,
    ) -> None:
        self._inv_repo = invitation_repo
        self._membership_repo = project_membership_repo
        self._role_repo = role_repo
        self._user_repo = user_repo

    # ------------------------------------------------------------------

    def execute(
        self,
        requester_id: UUID,
        project_id: UUID,
        status_filter: str = "pending",
    ) -> list[InvitationListItemDto]:
        """Return invitations for *project_id*, filtered by *status_filter*.

        Raises:
            PermissionDeniedError: requester is not a project member or superadmin.
        """
        requester = self._user_repo.find_by_id(requester_id)
        if requester is None:
            raise PermissionDeniedError(f"User {requester_id} not found.")

        is_superadmin = requester.has_permission("*", "*")
        is_member = self._membership_repo.exists(requester_id, project_id)
        if not is_superadmin and not is_member:
            raise PermissionDeniedError(
                f"User {requester_id} is not a member of project {project_id}."
            )

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
            role = self._role_repo.find_by_id(inv.role_id)
            role_name = role.name if role else str(inv.role_id)

            inviter = self._user_repo.find_by_id(inv.invited_by)
            inviter_name = inviter.display_or_email if inviter else str(inv.invited_by)

            result.append(
                InvitationListItemDto(
                    id=inv.id,
                    email=inv.email,
                    role_name=role_name,
                    status=inv.status,
                    expires_at=inv.expires_at,
                    created_at=inv.created_at,
                    invited_by_name=inviter_name,
                )
            )

        return result
