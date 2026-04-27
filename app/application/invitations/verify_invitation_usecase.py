"""VerifyInvitationUseCase — validate a raw token and return safe metadata."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from app.application.invitations.dtos import VerifyInvitationDto
from app.application.invitations.ports import (
    InvitationRepositoryPort,
    ProjectRepositoryPort,
    RoleRepositoryPort,
    UserWriteRepositoryPort,
)
from app.domain.entities.invitation import InvitationStatus
from app.domain.exceptions.invitation_exceptions import (
    InvalidInvitationTokenError,
    InvitationExpiredError,
    InvitationRevokedError,
    InvitationAlreadyAcceptedError,
)
from app.domain.value_objects.invite_token import hash_token


class VerifyInvitationUseCase:
    """Read-only use-case: verify token validity and expose safe invitation metadata."""

    def __init__(
        self,
        invitation_repo: InvitationRepositoryPort,
        project_repo: ProjectRepositoryPort,
        role_repo: RoleRepositoryPort,
        user_repo: UserWriteRepositoryPort,
    ) -> None:
        self._inv_repo = invitation_repo
        self._project_repo = project_repo
        self._role_repo = role_repo
        self._user_repo = user_repo

    # ------------------------------------------------------------------

    def execute(self, raw_token: str) -> VerifyInvitationDto:
        """Verify *raw_token* and return safe metadata for the accept-invite page.

        Raises:
            InvalidInvitationTokenError: token does not match any invitation.
            InvitationExpiredError: invitation found but past expiry.
            InvitationRevokedError: invitation was explicitly revoked.
            InvitationAlreadyAcceptedError: invitation already consumed.
        """
        token_hash = hash_token(raw_token)
        inv = self._inv_repo.find_by_token_hash(token_hash)
        if inv is None:
            raise InvalidInvitationTokenError("No invitation found for the supplied token.")

        # Lazy-expire: if still PENDING but past expiry, persist the flip
        if inv.status == InvitationStatus.PENDING and not inv.is_usable():
            expired = replace(
                inv,
                status=InvitationStatus.EXPIRED,
                updated_at=datetime.now(timezone.utc),
            )
            self._inv_repo.save(expired)
            raise InvitationExpiredError(f"Invitation {inv.id} has expired.")

        if inv.status == InvitationStatus.EXPIRED:
            raise InvitationExpiredError(f"Invitation {inv.id} has expired.")
        if inv.status == InvitationStatus.REVOKED:
            raise InvitationRevokedError(f"Invitation {inv.id} has been revoked.")
        if inv.status == InvitationStatus.ACCEPTED:
            raise InvitationAlreadyAcceptedError(f"Invitation {inv.id} was already accepted.")

        # Load related entities for the response DTO
        project = self._project_repo.find_by_id(inv.project_id)
        project_name = project.name if project else str(inv.project_id)

        role = self._role_repo.find_by_id(inv.role_id)
        role_name = role.name if role else str(inv.role_id)

        inviter = self._user_repo.find_by_id(inv.invited_by)
        inviter_name = inviter.display_or_email if inviter else str(inv.invited_by)

        return VerifyInvitationDto(
            email=inv.email,
            project_name=project_name,
            role_name=role_name,
            inviter_name=inviter_name,
            expires_at=inv.expires_at,
        )
