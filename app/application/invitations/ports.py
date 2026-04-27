"""Repository ports (Protocols) for the invitations application layer."""

from typing import Optional, Protocol
from uuid import UUID

from app.domain.entities.invitation import Invitation, InvitationStatus
from app.domain.entities.project_membership import ProjectMembership


class InvitationRepositoryPort(Protocol):
    """Persistence contract for Invitation aggregate."""

    def save(self, inv: Invitation) -> Invitation:
        """Persist an invitation (insert or update). Returns the saved instance."""
        ...

    def find_by_token_hash(self, token_hash: str) -> Optional[Invitation]:
        """Look up an invitation by its sha256 token hash. Returns None if not found."""
        ...

    def find_by_id(self, invitation_id: UUID) -> Optional[Invitation]:
        """Look up an invitation by its UUID. Returns None if not found."""
        ...

    def find_pending_by_email_and_project(
        self, email: str, project_id: UUID
    ) -> Optional[Invitation]:
        """
        Return the first PENDING invitation for the given email + project combination,
        or None if no such invitation exists.
        """
        ...

    def list_by_project(
        self,
        project_id: UUID,
        status: Optional[InvitationStatus] = None,
    ) -> list[Invitation]:
        """
        Return all invitations for a project, optionally filtered by status.

        Args:
            project_id: Target project UUID.
            status: If provided, only return invitations with this status.
        """
        ...

    def count_created_today_by_project(self, project_id: UUID) -> int:
        """
        Return the number of invitations created today (UTC) for the given project.

        Used to enforce the per-project daily rate limit (50/day).
        """
        ...


class ProjectMembershipRepositoryPort(Protocol):
    """Persistence contract for ProjectMembership aggregate."""

    def add(self, membership: ProjectMembership) -> ProjectMembership:
        """Persist a new project membership. Returns the saved instance."""
        ...

    def exists(self, user_id: UUID, project_id: UUID) -> bool:
        """Return True if the user is already a member of the project."""
        ...
