"""RevokeInvitationUseCase — idempotently revoke a pending invitation."""

from __future__ import annotations

from uuid import UUID

from app.application.invitations.exceptions import PermissionDeniedError
from app.application.invitations.ports import (
    InvitationRepositoryPort,
    UserWriteRepositoryPort,
)
from app.domain.entities.invitation import InvitationStatus
from app.domain.exceptions.invitation_exceptions import InvitationNotFoundError


class RevokeInvitationUseCase:
    """Revoke an invitation; no-op if already non-pending."""

    def __init__(
        self,
        invitation_repo: InvitationRepositoryPort,
        user_repo: UserWriteRepositoryPort,
    ) -> None:
        self._inv_repo = invitation_repo
        self._user_repo = user_repo

    # ------------------------------------------------------------------

    def execute(self, inviter_id: UUID, invitation_id: UUID) -> None:
        """Revoke *invitation_id* on behalf of *inviter_id*.

        Raises:
            InvitationNotFoundError: no invitation with that ID.
            PermissionDeniedError: actor lacks invite permission.
        """
        inv = self._inv_repo.find_by_id(invitation_id)
        if inv is None:
            raise InvitationNotFoundError(f"Invitation {invitation_id} not found.")

        inviter = self._user_repo.find_by_id(inviter_id)
        if inviter is None:
            raise PermissionDeniedError(f"User {inviter_id} not found.")

        if not self._can_revoke(inviter, inv.project_id, inv.invited_by):
            raise PermissionDeniedError(f"User {inviter_id} cannot revoke invitation {invitation_id}.")

        # Idempotent: nothing to do if already non-pending
        if inv.status != InvitationStatus.PENDING:
            return

        revoked = inv.revoke()
        self._inv_repo.save(revoked)

    # ------------------------------------------------------------------

    @staticmethod
    def _can_revoke(user, project_id: UUID, original_inviter_id: UUID) -> bool:  # type: ignore[override]
        """True if user has superadmin/project:invite permission or is the original inviter."""
        if user.has_permission("*", "*"):
            return True
        if user.has_permission("project", "invite"):
            return True
        if user.id == original_inviter_id:
            return True
        return False
