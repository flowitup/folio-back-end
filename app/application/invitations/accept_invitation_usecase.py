"""AcceptInvitationUseCase — create account + membership in a single transaction."""

from __future__ import annotations


from app.application.invitations.dtos import AcceptInvitationResultDto
from app.application.invitations.ports import (
    InvitationRepositoryPort,
    ProjectMembershipRepositoryPort,
    RoleRepositoryPort,
    TransactionalSessionPort,
    UserWriteRepositoryPort,
)
from app.application.ports.password_hasher import PasswordHasherPort
from app.application.ports.token_issuer import TokenIssuerPort
from app.domain.entities.project_membership import ProjectMembership
from app.domain.entities.user import User
from app.domain.exceptions.invitation_exceptions import InvalidInvitationTokenError
from app.domain.value_objects.invite_token import hash_token

_MIN_PASSWORD_LEN = 8
_MAX_PASSWORD_LEN = 128
_MAX_NAME_LEN = 100


class AcceptInvitationUseCase:
    """Accept an invitation: create user + membership, return JWT pair."""

    _DEFAULT_GLOBAL_ROLE = "user"

    def __init__(
        self,
        invitation_repo: InvitationRepositoryPort,
        user_repo: UserWriteRepositoryPort,
        project_membership_repo: ProjectMembershipRepositoryPort,
        password_hasher: PasswordHasherPort,
        token_issuer: TokenIssuerPort,
        db_session: TransactionalSessionPort,
        role_repo: RoleRepositoryPort,
    ) -> None:
        self._inv_repo = invitation_repo
        self._user_repo = user_repo
        self._membership_repo = project_membership_repo
        self._hasher = password_hasher
        self._tokens = token_issuer
        self._db = db_session
        self._role_repo = role_repo

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
        password_hash = self._hasher.hash(password)

        # --- Transactional block (SAVEPOINT — works inside Flask-SQLAlchemy's request transaction).
        #
        # M1 (from code-review): the lookup uses a row-level lock so two concurrent
        # POST /accept requests for the same valid token serialize at the DB layer.
        # The first commit wins; the second sees status=ACCEPTED on re-read and
        # raises InvitationAlreadyAcceptedError via inv.accept().
        #
        # Order matters: inv.accept() runs BEFORE user/membership creation so a
        # not-usable invitation rolls back the savepoint without leaving an
        # orphan user behind.
        with self._db.begin_nested():
            inv = self._inv_repo.find_by_token_hash_for_update(token_hash)
            if inv is None:
                raise InvalidInvitationTokenError("No invitation found for the supplied token.")
            accepted_inv = inv.accept()  # raises if expired/revoked/accepted

            user = self._user_repo.find_by_email(inv.email)
            is_new_user = user is None
            if is_new_user:
                user = User.create(
                    email=inv.email,
                    password_hash=password_hash,
                    display_name=name,
                )
                user = self._user_repo.save(user)

                default_role = self._role_repo.find_by_name(self._DEFAULT_GLOBAL_ROLE)
                if default_role is not None:
                    self._user_repo.assign_role(user.id, default_role.id)

            if not self._membership_repo.exists(user.id, inv.project_id):
                membership = ProjectMembership.create(
                    user_id=user.id,
                    project_id=inv.project_id,
                    role_id=inv.role_id,
                    invited_by=inv.invited_by,
                )
                self._membership_repo.add(membership)

            self._inv_repo.save(accepted_inv)
        self._db.commit()

        # Re-read user after commit so role assignments are visible
        fresh_user = self._user_repo.find_by_id(user.id)
        permissions: list[str] = []
        if fresh_user is not None:
            for role in fresh_user.roles:
                for perm in role.permissions:
                    permissions.append(perm.name)
            permissions = list(set(permissions))

        access_token = self._tokens.create_access_token(user.id, {"permissions": permissions})
        refresh_token = self._tokens.create_refresh_token(user.id)

        return AcceptInvitationResultDto(
            user=fresh_user or user,
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
            raise ValueError(f"Password must be between {_MIN_PASSWORD_LEN} and " f"{_MAX_PASSWORD_LEN} characters.")

    @staticmethod
    def _validate_name(name: str) -> None:
        stripped = name.strip()
        if not stripped or len(stripped) > _MAX_NAME_LEN:
            raise ValueError(f"Name must be between 1 and {_MAX_NAME_LEN} characters.")
