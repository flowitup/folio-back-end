"""RevokeInvitationUseCase — idempotently revoke a pending invitation."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.application.invitations.authz import can_manage_project_invites
from app.application.invitations.exceptions import PermissionDeniedError
from app.application.invitations.ports import (
    InvitationRepositoryPort,
    TransactionalSessionPort,
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
        db_session: TransactionalSessionPort,
        authz_reader: Any = None,  # AuthzReaderPort — resolves project:invite
    ) -> None:
        self._inv_repo = invitation_repo
        self._user_repo = user_repo
        self._db = db_session
        self._authz_reader = authz_reader

    def set_authz_reader(self, reader: Any) -> None:
        """Inject the resolver read port after construction.

        `wiring.configure_container()` builds this use-case before the
        SQLAlchemy-backed reader exists; the app factory calls this once it does.
        """
        self._authz_reader = reader

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

        if not self._can_revoke(inviter_id, inv.project_id, inv.invited_by):
            raise PermissionDeniedError(f"User {inviter_id} cannot revoke invitation {invitation_id}.")

        # Idempotent: nothing to do if already non-pending
        if inv.status != InvitationStatus.PENDING:
            return

        revoked = inv.revoke()
        self._inv_repo.save(revoked)
        # Repository.save() only flushes — the request-scoped Flask-SQLAlchemy
        # session rolls back on teardown without an explicit commit, so revoke
        # would silently no-op.
        self._db.commit()

    # ------------------------------------------------------------------

    def _can_revoke(self, user_id: UUID, project_id: UUID, original_inviter_id: UUID) -> bool:
        """True when the resolver grants `project:invite` on THIS invitation's project.

        Scoped per project, so a manager of one company can no longer revoke an
        invitation belonging to another (the legacy global role that allowed it
        is not consulted any more). The person who sent the invitation may
        always take it back.
        """
        if user_id == original_inviter_id:
            return True
        return can_manage_project_invites(self._authz_reader, user_id, project_id)
