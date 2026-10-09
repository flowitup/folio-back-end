"""AcceptInvitationUseCase — create account + membership in a single transaction."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING, Optional
from uuid import UUID

from app.application.invitations.dtos import AcceptInvitationResultDto
from app.application.invitations.ports import (
    InvitationRepositoryPort,
    ProjectMembershipRepositoryPort,
    TransactionalSessionPort,
)
from app.application.ports.login_otp_repository import LoginOtpRepositoryPort
from app.application.ports.token_issuer import TokenIssuerPort
from app.application.ports.user_repository import UserRepositoryPort
from app.application.usecases.otp_login import (
    RequestOtpResult,
    RequestOtpUseCase,
    RequestSignupOtpUseCase,
    _consume_code,
)
from app.application.company_persons.ensure_company_person import ensure_company_person
from app.domain.companies.roles import CompanyRole
from app.domain.companies.user_company_access import UserCompanyAccess
from app.domain.entities.invitation import Invitation
from app.domain.entities.project_membership import ProjectMembership
from app.domain.entities.user import User
from app.domain.exceptions.auth_exceptions import OtpInvalidError, PhoneAlreadyRegisteredError
from app.domain.exceptions.invitation_exceptions import (
    InvalidInvitationTokenError,
    InvitationAccountExistsError,
    InvitationWrongAccountError,
)
from app.domain.value_objects.invite_token import hash_token
from app.domain.value_objects.phone_number import normalize_french_phone

if TYPE_CHECKING:
    from app.application.authz.ports import AuthzReaderPort
    from app.application.companies.ports import UserCompanyAccessRepositoryPort
    from app.application.company_persons.link_person_on_signup_usecase import LinkPersonOnSignupUseCase

_MAX_NAME_LEN = 100


class RequestInviteOtpUseCase:
    """Text a code to the phone number an invitation acceptor is claiming.

    Public endpoint — the invitation token is the authorisation, so a code is only sent for a
    currently-usable (pending, unexpired, unrevoked) invitation. Once the token checks out, this
    delegates straight to ``RequestSignupOtpUseCase``: same phone normalisation,
    already-registered check, per-phone throttling and code storage as phone sign-up
    (``_issue_code`` in otp_login.py). No OTP logic is reimplemented here.

    When the invited address already has an account (a second invitation sent before the
    first was accepted, say), the invitation belongs to that account: its own phone gets a
    sign-in code (``RequestOtpUseCase``), and any other phone is refused.
    """

    def __init__(
        self,
        invitation_repo: InvitationRepositoryPort,
        request_signup_otp: RequestSignupOtpUseCase,
        user_repo: Optional[UserRepositoryPort] = None,
        request_signin_otp: Optional[RequestOtpUseCase] = None,
    ) -> None:
        self._inv_repo = invitation_repo
        self._request_signup_otp = request_signup_otp
        self._users = user_repo
        self._request_signin_otp = request_signin_otp

    def execute(self, raw_token: str, raw_phone: str) -> RequestOtpResult:
        """Send a code to ``raw_phone`` once ``raw_token`` proves a live invitation.

        Raises:
            InvalidInvitationTokenError: token does not match any invitation.
            InvitationExpiredError / InvitationRevokedError / InvitationAlreadyAcceptedError:
                the invitation exists but cannot be used right now (via ``Invitation.accept()``;
                the returned copy is discarded here — nothing is persisted by this check).
            InvitationAccountExistsError: the invited address has an account and ``raw_phone``
                is not its phone.
            InvalidPhoneNumberError / PhoneAlreadyRegisteredError / OtpThrottledError / SmsSendError:
                propagated from the sign-up / sign-in code use cases.
        """
        inv = self._inv_repo.find_by_token_hash(hash_token(raw_token))
        if inv is None:
            raise InvalidInvitationTokenError("No invitation found for the supplied token.")
        inv.accept()  # validate-only: raises on expired/revoked/accepted; result discarded

        account = self._users.find_by_email(inv.email) if self._users is not None else None
        if account is None:
            return self._request_signup_otp.execute(raw_phone)

        phone = normalize_french_phone(raw_phone)
        if self._request_signin_otp is None or not account.phone or account.phone != phone:
            raise InvitationAccountExistsError("This invitation is for an existing account; use its phone number.")
        return self._request_signin_otp.execute(phone)


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

    `execute` takes a `phone` proven by a 6-digit SMS code (see
    `otp_login._consume_code`) rather than a chosen password: a newly created
    account gets that verified number written to `users.phone` so it can sign
    in the same way as phone sign-up. `link_person_on_signup` stays injectable
    for parity with the OTP sign-up wiring but is not invoked here — linking a
    fresh account to pending company-directory profiles is a separate concern
    from proving phone ownership for invitation acceptance, and is out of this
    change's scope.
    """

    def __init__(
        self,
        invitation_repo: InvitationRepositoryPort,
        user_repo: UserRepositoryPort,
        project_membership_repo: ProjectMembershipRepositoryPort,
        token_issuer: TokenIssuerPort,
        db_session: TransactionalSessionPort,
        authz_reader: "Optional[AuthzReaderPort]" = None,
        access_repo: "Optional[UserCompanyAccessRepositoryPort]" = None,
        link_person_on_signup: "Optional[LinkPersonOnSignupUseCase]" = None,
        person_repo: "Optional[Any]" = None,
        company_person_repo: "Optional[Any]" = None,
        otp_repo: Optional[LoginOtpRepositoryPort] = None,
        otp_max_attempts: int = 5,
    ) -> None:
        self._inv_repo = invitation_repo
        self._user_repo = user_repo
        self._membership_repo = project_membership_repo
        self._tokens = token_issuer
        self._db = db_session
        self._authz_reader = authz_reader
        self._access_repo = access_repo
        self._link_person_on_signup = link_person_on_signup
        # Directory repositories: the acceptor must be listed among the
        # company's people, or an admin cannot assign them to a project.
        self._persons = person_repo
        self._company_persons = company_person_repo
        # Verifies the phone code proving this acceptance (see execute()).
        self._otp_repo = otp_repo
        self._otp_max_attempts = otp_max_attempts

    # ------------------------------------------------------------------

    def execute(
        self,
        raw_token: str,
        name: str,
        phone: str,
        code: str,
    ) -> AcceptInvitationResultDto:
        """Process acceptance of an invitation.

        Raises:
            InvalidInvitationTokenError: token unknown.
            InvitationExpiredError / InvitationRevokedError / InvitationAlreadyAcceptedError:
                via inv.accept().
            InvalidPhoneNumberError: phone is not a valid French E.164 number.
            OtpInvalidError: code is wrong, expired, attempts exhausted, or was issued to sign an
                existing account in while the invited address has no account yet.
            InvitationAccountExistsError: the invited address already has an account and the code
                was not issued to sign that account in (it proves some other phone).
            PhoneAlreadyRegisteredError: phone already belongs to another account.
            ValueError: name validation failure.
        """
        if self._otp_repo is None:
            raise RuntimeError("AcceptInvitationUseCase requires otp_repo to verify a phone code.")

        # Validate inputs before hitting the DB
        self._validate_name(name)
        normalized_phone = normalize_french_phone(phone)
        token_hash = hash_token(raw_token)

        # The invitation token is this endpoint's authorisation too: reject a bad, expired,
        # revoked or already-accepted token BEFORE spending an attempt against the phone code
        # (Invitation.accept() raises the right typed error; the returned copy is discarded —
        # the real, row-locked acceptance happens below).
        early_inv = self._inv_repo.find_by_token_hash(token_hash)
        if early_inv is None:
            raise InvalidInvitationTokenError("No invitation found for the supplied token.")
        early_inv.accept()

        # Reuses the exact same comparison, hashing, expiry and attempt-counting as sign-in and
        # phone sign-up (_consume_code in otp_login.py) — including the non-production
        # OTP_TEST_CODE bypass, which invitation acceptance inherits for free.
        otp = _consume_code(
            self._otp_repo,
            phone=normalized_phone,
            code=code,
            now=datetime.now(timezone.utc),
            max_attempts=self._otp_max_attempts,
        )

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
                if otp.user_id is not None:
                    # A sign-in code proves an existing account, not a new one.
                    raise OtpInvalidError("Invalid or expired code")
                if self._user_repo.find_by_phone(normalized_phone) is not None:
                    raise PhoneAlreadyRegisteredError("This phone number already has an account.")
                user = User.create(
                    email=inv.email,
                    display_name=name,
                    phone=normalized_phone,
                )
                user = self._user_repo.save(user)
            elif otp.user_id != user.id or not user.is_active:
                # The invited address already has an account: only a code that signs THAT
                # account in may accept for it. A code proving any other phone would hand
                # out a session for someone else's account.
                raise InvitationAccountExistsError("This invitation is for an existing account; use its phone number.")

            self._join_project(inv, user)
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
            invited_by=accepted_inv.invited_by,
            project_id=accepted_inv.project_id,
        )

    def accept_as(self, raw_token: str, user_id: UUID) -> Invitation:
        """Accept an invitation for an account that is already signed in; return the accepted invitation.

        The session proves the account, so no phone code is needed — but only the
        account the invitation was sent to may take it up.

        Raises:
            InvalidInvitationTokenError: token unknown.
            InvitationExpiredError / InvitationRevokedError / InvitationAlreadyAcceptedError:
                via inv.accept().
            InvitationWrongAccountError: the signed-in account's email is not the invited one.
        """
        with self._db.begin_nested():
            inv = self._inv_repo.find_by_token_hash_for_update(hash_token(raw_token))
            if inv is None:
                raise InvalidInvitationTokenError("No invitation found for the supplied token.")
            accepted_inv = inv.accept()  # raises if expired/revoked/accepted

            user = self._user_repo.find_by_id(user_id)
            if user is None or (user.email or "").strip().lower() != inv.email.strip().lower():
                raise InvitationWrongAccountError("This invitation was sent to another email address.")

            self._join_project(inv, user)
            self._inv_repo.save(accepted_inv)
        self._db.commit()
        return accepted_inv

    def _join_project(self, inv: Invitation, user: User) -> None:
        """Write what accepting grants: the project assignment, company access and directory profile."""
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

    # ------------------------------------------------------------------
    # Private validators
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_name(name: str) -> None:
        stripped = name.strip()
        if not stripped or len(stripped) > _MAX_NAME_LEN:
            raise ValueError(f"Name must be between 1 and {_MAX_NAME_LEN} characters.")
