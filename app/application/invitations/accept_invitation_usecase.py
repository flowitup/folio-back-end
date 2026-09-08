"""AcceptInvitationUseCase — create account + membership in a single transaction."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING, Optional

from app.application.invitations.dtos import AcceptInvitationResultDto
from app.application.invitations.ports import (
    InvitationRepositoryPort,
    ProjectMembershipRepositoryPort,
    TransactionalSessionPort,
    UserWriteRepositoryPort,
)
from app.application.ports.password_hasher import PasswordHasherPort
from app.application.ports.token_issuer import TokenIssuerPort
from app.application.company_persons.ensure_company_person import ensure_company_person
from app.domain.companies.roles import CompanyRole
from app.domain.companies.user_company_access import UserCompanyAccess
from app.domain.entities.project_membership import ProjectMembership
from app.domain.entities.user import User
from app.domain.exceptions.invitation_exceptions import InvalidInvitationTokenError
from app.domain.value_objects.invite_token import hash_token

if TYPE_CHECKING:
    from app.application.authz.ports import AuthzReaderPort
    from app.application.companies.ports import UserCompanyAccessRepositoryPort
    from app.application.company_persons.link_person_on_signup_usecase import LinkPersonOnSignupUseCase

_MIN_PASSWORD_LEN = 8
_MAX_PASSWORD_LEN = 128
_MAX_NAME_LEN = 100


class AcceptInvitationUseCase:
    """Accept an invitation: create user + membership, return JWT pair.

    Invitations stay the OUTSIDER path (Phase 2): no company-membership
    precondition on the acceptor. Once accepted, the acceptor becomes a
    `member` of the invited project's company (derived from the project —
    invitations carry no `company_id` column, no schema change in this
    slice) in addition to the existing project assignment (`user_projects`
    row added below). `authz_reader`/`access_repo` are optional so existing
    callers/tests that construct this use case without the companies BC
    keep working unchanged (company attachment is then simply skipped).

    M3: `execute` takes no `phone` parameter — an invitation acceptor typing
    an arbitrary phone number is never a verified identity, so it must never
    be written to `users.phone` nor fed to `LinkPersonOnSignupUseCase` (which
    would otherwise silently merge an unrelated pending onboarding profile
    onto this account). `link_person_on_signup` stays injectable for parity
    with the OTP sign-up wiring but is currently unused here for that reason.
    """

    def __init__(
        self,
        invitation_repo: InvitationRepositoryPort,
        user_repo: UserWriteRepositoryPort,
        project_membership_repo: ProjectMembershipRepositoryPort,
        password_hasher: PasswordHasherPort,
        token_issuer: TokenIssuerPort,
        db_session: TransactionalSessionPort,
        authz_reader: "Optional[AuthzReaderPort]" = None,
        access_repo: "Optional[UserCompanyAccessRepositoryPort]" = None,
        link_person_on_signup: "Optional[LinkPersonOnSignupUseCase]" = None,
        person_repo: "Optional[Any]" = None,
        company_person_repo: "Optional[Any]" = None,
    ) -> None:
        self._inv_repo = invitation_repo
        self._user_repo = user_repo
        self._membership_repo = project_membership_repo
        self._hasher = password_hasher
        self._tokens = token_issuer
        self._db = db_session
        self._authz_reader = authz_reader
        self._access_repo = access_repo
        self._link_person_on_signup = link_person_on_signup
        # Directory repositories: the acceptor must be listed among the
        # company's people, or an admin cannot assign them to a project.
        self._persons = person_repo
        self._company_persons = company_person_repo

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

            if not self._membership_repo.exists(user.id, inv.project_id):
                membership = ProjectMembership.create(
                    user_id=user.id,
                    project_id=inv.project_id,
                    invited_by=inv.invited_by,
                )
                self._membership_repo.add(membership)

            # Invitations are the outsider path (Phase 2): no company-membership
            # precondition, but the acceptor becomes a `member` of the invited
            # project's company (derived from the project — invitations carry
            # no company_id of their own).
            if self._authz_reader is not None and self._access_repo is not None:
                company_id = self._authz_reader.project_company_id(inv.project_id)
                if company_id is not None and self._access_repo.find(user.id, company_id) is None:
                    self._access_repo.save(
                        UserCompanyAccess(
                            user_id=user.id,
                            company_id=company_id,
                            is_primary=len(self._access_repo.list_for_user(user.id)) == 0,
                            attached_at=datetime.now(timezone.utc),
                            role=CompanyRole.MEMBER.value,
                        )
                    )
                if company_id is not None:
                    ensure_company_person(
                        persons=self._persons,
                        company_persons=self._company_persons,
                        users=self._user_repo,
                        user_id=user.id,
                        company_id=company_id,
                        now=datetime.now(timezone.utc),
                    )

            self._inv_repo.save(accepted_inv)
        self._db.commit()

        # Re-read the user after commit so the company attachment is visible.
        fresh_user = self._user_repo.find_by_id(user.id)

        # Identity only: permissions are resolved per request, never carried
        # in the token.
        access_token = self._tokens.create_access_token(user.id)
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
