"""AcceptInvitationUseCase — create account + membership in a single transaction."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.application.invitations.dtos import AcceptInvitationResultDto
from app.application.invitations.ports import (
    InvitationRepositoryPort,
    ProjectMembershipRepositoryPort,
    UserWriteRepositoryPort,
)
from app.application.ports.password_hasher import PasswordHasherPort
from app.application.ports.token_issuer import TokenIssuerPort
from app.domain.entities.project_membership import ProjectMembership
from app.domain.entities.user import User
from app.domain.exceptions.invitation_exceptions import InvalidInvitationTokenError
from app.domain.value_objects.invite_token import hash_token
from tasks import EmailPayload


_MIN_PASSWORD_LEN = 8
_MAX_PASSWORD_LEN = 128
_MAX_NAME_LEN = 100


class AcceptInvitationUseCase:
    """Accept an invitation: create user + membership, return JWT pair."""

    def __init__(
        self,
        invitation_repo: InvitationRepositoryPort,
        user_repo: UserWriteRepositoryPort,
        project_membership_repo: ProjectMembershipRepositoryPort,
        password_hasher: PasswordHasherPort,
        token_issuer: TokenIssuerPort,
        db_session: Any,  # Protocol: .begin() context manager; commit/rollback managed by with-block
    ) -> None:
        self._inv_repo = invitation_repo
        self._user_repo = user_repo
        self._membership_repo = project_membership_repo
        self._hasher = password_hasher
        self._tokens = token_issuer
        self._db = db_session

    # ------------------------------------------------------------------

    def execute(
        self,
        raw_token: str,
        name: str,
        password: str,
    ) -> AcceptInvitationResultDto:
        """Process acceptance of an invitation.

        Raises:
            InvalidInvitationTokenError: token unknown.
            InvitationExpiredError / InvitationRevokedError / InvitationAlreadyAcceptedError:
                via inv.accept().
            ValueError: password or name validation failure.
        """
        # Validate inputs before hitting the DB
        self._validate_name(name)
        self._validate_password(password)

        token_hash = hash_token(raw_token)
        inv = self._inv_repo.find_by_token_hash(token_hash)
        if inv is None:
            raise InvalidInvitationTokenError("No invitation found for the supplied token.")

        # Will raise if expired/revoked/accepted (domain exceptions)
        _ = inv.is_usable()  # warm check; accept() below enforces state

        password_hash = self._hasher.hash(password)

        # --- Transactional block (SAVEPOINT — works inside Flask-SQLAlchemy's request transaction) ---
        with self._db.begin_nested():
            # Race condition guard: user may have registered between verify and accept
            user = self._user_repo.find_by_email(inv.email)
            if user is None:
                user = User.create(
                    email=inv.email,
                    password_hash=password_hash,
                    display_name=name,
                )
                user = self._user_repo.save(user)

            if not self._membership_repo.exists(user.id, inv.project_id):
                membership = ProjectMembership.create(
                    user_id=user.id,
                    project_id=inv.project_id,
                    role_id=inv.role_id,
                    invited_by=inv.invited_by,
                )
                self._membership_repo.add(membership)

            accepted_inv = inv.accept()
            self._inv_repo.save(accepted_inv)
        self._db.commit()

        # Issue tokens after commit (outside the transaction)
        access_token = self._tokens.create_access_token(user.id)
        refresh_token = self._tokens.create_refresh_token(user.id)

        return AcceptInvitationResultDto(
            user=user,
            access_token=access_token,
            refresh_token=refresh_token,
        )

    # ------------------------------------------------------------------
    # Private validators
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_password(password: str) -> None:
        length = len(password)
        if length < _MIN_PASSWORD_LEN or length > _MAX_PASSWORD_LEN:
            raise ValueError(
                f"Password must be between {_MIN_PASSWORD_LEN} and "
                f"{_MAX_PASSWORD_LEN} characters."
            )

    @staticmethod
    def _validate_name(name: str) -> None:
        stripped = name.strip()
        if not stripped or len(stripped) > _MAX_NAME_LEN:
            raise ValueError(
                f"Name must be between 1 and {_MAX_NAME_LEN} characters."
            )
