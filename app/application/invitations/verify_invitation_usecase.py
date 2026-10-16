"""VerifyInvitationUseCase — validate a raw token and return safe metadata."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.application.invitations.dtos import VerifyInvitationDto
from app.application.invitations.ports import (
    InvitationRepositoryPort,
    ProjectRepositoryPort,
    UserWriteRepositoryPort,
)
from app.domain.companies.roles import CompanyRole
from app.domain.entities.invitation import Invitation, InvitationStatus
from app.domain.entities.project import project_display_label
from app.domain.exceptions.invitation_exceptions import (
    InvalidInvitationTokenError,
    InvitationExpiredError,
    InvitationRevokedError,
    InvitationAlreadyAcceptedError,
    InvitationSupersededError,
)
from app.domain.value_objects.invite_token import hash_token

# Re-sending an invitation revokes the pending one and creates its successor in the same request.
_SUPERSEDED_WINDOW = timedelta(seconds=30)


class VerifyInvitationUseCase:
    """Read-only use-case: verify token validity and expose safe invitation metadata."""

    def __init__(
        self,
        invitation_repo: InvitationRepositoryPort,
        project_repo: ProjectRepositoryPort,
        user_repo: UserWriteRepositoryPort,
    ) -> None:
        self._inv_repo = invitation_repo
        self._project_repo = project_repo
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
            raise InvitationExpiredError("This invitation has expired.")

        # Public endpoint: the messages carry no internal id.
        if inv.status == InvitationStatus.EXPIRED:
            raise InvitationExpiredError("This invitation has expired.")
        if inv.status == InvitationStatus.REVOKED:
            if self._was_superseded(inv):
                raise InvitationSupersededError("A newer invitation was sent to this address; use the latest e-mail.")
            raise InvitationRevokedError("This invitation has been revoked.")
        if inv.status == InvitationStatus.ACCEPTED:
            raise InvitationAlreadyAcceptedError("This invitation was already accepted.")

        # Load related entities for the response DTO
        project = self._project_repo.find_by_id(inv.project_id)
        # Named the way the app will show it once joined: by its address.
        project_name = project_display_label(project) if project else str(inv.project_id)

        inviter = self._user_repo.find_by_id(inv.invited_by)
        inviter_name = inviter.display_or_email if inviter else str(inv.invited_by)

        return VerifyInvitationDto(
            email=inv.email,
            project_name=project_name,
            # Accepting makes the invitee a company `member`; the invitation
            # itself carries no role.
            role_name=CompanyRole.MEMBER.value,
            inviter_name=inviter_name,
            expires_at=inv.expires_at,
        )

    def _was_superseded(self, inv: Invitation) -> bool:
        """Whether ``inv`` was revoked by a re-send: a newer invitation to the same address and
        project was created when it was revoked (CreateInvitationUseCase does both in one request).
        An admin's explicit revoke later on creates no successor and stays a plain revocation.
        """
        if inv.updated_at is None:
            return False
        email = inv.email.strip().lower()
        try:
            siblings = self._inv_repo.list_by_project(inv.project_id)
            return any(
                other.id != inv.id
                and other.email.strip().lower() == email
                and other.created_at > inv.created_at
                and abs(other.created_at - inv.updated_at) <= _SUPERSEDED_WINDOW
                for other in siblings
            )
        except Exception:  # a lookup failure must not turn a revoked link into a 500
            return False
