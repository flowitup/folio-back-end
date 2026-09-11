"""Unit tests for AcceptInvitationUseCase."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.invitations.accept_invitation_usecase import AcceptInvitationUseCase
from app.application.usecases.otp_login import _hash_code
from app.domain.entities.invitation import Invitation, InvitationStatus
from app.domain.entities.login_otp import LoginOtp
from app.domain.entities.user import User
from app.domain.exceptions.auth_exceptions import OtpInvalidError, PhoneAlreadyRegisteredError
from app.domain.exceptions.invitation_exceptions import (
    InvitationAlreadyAcceptedError,
    InvitationExpiredError,
    InvalidInvitationTokenError,
)
from app.domain.value_objects.invite_token import generate_token
from app.domain.value_objects.phone_number import InvalidPhoneNumberError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# The phone + code every _make_uc() otp_repo accepts unless a test overrides it.
PHONE = "+33611223344"
CODE = "424242"


def _make_pending_inv() -> tuple[Invitation, str]:
    raw, token_hash = generate_token()
    now = datetime.now(timezone.utc)
    inv = Invitation(
        id=uuid4(),
        email="invitee@example.com",
        project_id=uuid4(),
        token_hash=token_hash,
        status=InvitationStatus.PENDING,
        expires_at=now + timedelta(days=7),
        invited_by=uuid4(),
        created_at=now,
        updated_at=now,
    )
    return inv, raw


def _make_expired_inv() -> tuple[Invitation, str]:
    raw, token_hash = generate_token()
    now = datetime.now(timezone.utc)
    inv = Invitation(
        id=uuid4(),
        email="invitee@example.com",
        project_id=uuid4(),
        token_hash=token_hash,
        status=InvitationStatus.PENDING,
        expires_at=now - timedelta(hours=1),  # already expired
        invited_by=uuid4(),
        created_at=now,
        updated_at=now,
    )
    return inv, raw


class _FakeSession:
    """Minimal db-session stub: supports `with session.begin_nested():` (savepoint) + `commit()`.

    Mirrors the SQLAlchemy scoped-session API used by AcceptInvitationUseCase under
    Flask-SQLAlchemy where the request transaction is already open.
    """

    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0

    @contextmanager
    def begin_nested(self):
        try:
            yield self
        except Exception:
            self.rollback_calls += 1
            raise

    def commit(self):
        self.commit_calls += 1


def _make_otp_repo(phone: str = PHONE, code: str = CODE, *, attempts: int = 0) -> MagicMock:
    """A LoginOtpRepositoryPort mock whose latest_for_phone(phone) returns a valid,
    unconsumed sign-up code matching `code` — the shape _consume_code() (otp_login.py)
    expects. user_id=None: a sign-up code proves a phone for a NOT-YET-existing
    account, exactly like AcceptInvitationUseCase's own acceptor.
    """
    otp = LoginOtp(
        id=uuid4(),
        user_id=None,
        phone=phone,
        code_hash=_hash_code(phone, code),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        created_at=datetime.now(timezone.utc),
        attempts=attempts,
    )
    repo = MagicMock()
    repo.latest_for_phone.return_value = otp
    return repo


def _make_uc(
    inv_repo=None,
    user_repo=None,
    membership_repo=None,
    otp_repo=None,
    token_issuer=None,
    db_session=None,
) -> AcceptInvitationUseCase:
    issuer = token_issuer or MagicMock()
    issuer.create_access_token.return_value = "access-jwt"
    issuer.create_refresh_token.return_value = "refresh-jwt"
    ur = user_repo or MagicMock()
    if isinstance(ur, MagicMock) and isinstance(ur.find_by_id.return_value, MagicMock):
        ur.find_by_id.return_value = _make_user()
    if isinstance(ur, MagicMock) and isinstance(ur.find_by_phone.return_value, MagicMock):
        ur.find_by_phone.return_value = None  # phone unclaimed unless a test says otherwise
    return AcceptInvitationUseCase(
        invitation_repo=inv_repo or MagicMock(),
        user_repo=ur,
        project_membership_repo=membership_repo or MagicMock(),
        token_issuer=issuer,
        db_session=db_session or _FakeSession(),
        otp_repo=otp_repo or _make_otp_repo(),
        otp_max_attempts=5,
    )


def _make_user(email: str = "invitee@example.com") -> User:
    return User(
        id=uuid4(),
        email=email,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        display_name="Invitee",
    )


# ---------------------------------------------------------------------------
# Happy path: new user
# ---------------------------------------------------------------------------


class TestAcceptNewUser:
    def test_creates_user_with_display_name(self):
        inv, raw = _make_pending_inv()
        new_user = _make_user(inv.email)
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_email.return_value = None  # new user
        user_repo.save.return_value = new_user
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, membership_repo=membership_repo)
        uc.execute(raw_token=raw, name="Alice", phone=PHONE, code=CODE)

        user_repo.save.assert_called_once()
        saved_user_arg = user_repo.save.call_args[0][0]
        assert saved_user_arg.display_name == "Alice"
        assert saved_user_arg.phone == PHONE

    def test_creates_membership(self):
        inv, raw = _make_pending_inv()
        new_user = _make_user(inv.email)
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_email.return_value = None
        user_repo.save.return_value = new_user
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, membership_repo=membership_repo)
        uc.execute(raw_token=raw, name="Alice", phone=PHONE, code=CODE)

        membership_repo.add.assert_called_once()

    def test_marks_invitation_accepted(self):
        inv, raw = _make_pending_inv()
        new_user = _make_user(inv.email)
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_email.return_value = None
        user_repo.save.return_value = new_user
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, membership_repo=membership_repo)
        uc.execute(raw_token=raw, name="Alice", phone=PHONE, code=CODE)

        inv_repo.save.assert_called_once()
        saved_inv = inv_repo.save.call_args[0][0]
        assert saved_inv.status == InvitationStatus.ACCEPTED

    def test_returns_auth_tokens_without_permission_claims(self):
        inv, raw = _make_pending_inv()
        new_user = _make_user(inv.email)
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_email.return_value = None
        user_repo.save.return_value = new_user
        user_repo.find_by_id.return_value = new_user
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False
        issuer = MagicMock()
        issuer.create_access_token.return_value = "access-jwt"
        issuer.create_refresh_token.return_value = "refresh-jwt"

        uc = _make_uc(
            inv_repo=inv_repo,
            user_repo=user_repo,
            membership_repo=membership_repo,
            token_issuer=issuer,
        )
        result = uc.execute(raw_token=raw, name="Alice", phone=PHONE, code=CODE)

        assert result.access_token == "access-jwt"
        assert result.refresh_token == "refresh-jwt"
        # Identity only: permissions are resolved per request, never minted here.
        issuer.create_access_token.assert_called_once_with(new_user.id)


# ---------------------------------------------------------------------------
# Race condition: existing user with same email
# ---------------------------------------------------------------------------


class TestExistingUserRace:
    def test_reuses_existing_user_without_creating_a_new_one(self):
        inv, raw = _make_pending_inv()
        existing_user = _make_user(inv.email)
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_email.return_value = existing_user  # already exists
        user_repo.find_by_id.return_value = existing_user
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, membership_repo=membership_repo)
        result = uc.execute(raw_token=raw, name="Alice", phone=PHONE, code=CODE)

        # Must NOT call user_repo.save (no user creation) — the existing account's
        # own phone is untouched by someone else's invitation acceptance.
        user_repo.save.assert_not_called()
        assert result.user == existing_user

    def test_attaches_membership_for_existing_user(self):
        inv, raw = _make_pending_inv()
        existing_user = _make_user(inv.email)
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_email.return_value = existing_user
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, membership_repo=membership_repo)
        uc.execute(raw_token=raw, name="Alice", phone=PHONE, code=CODE)

        membership_repo.add.assert_called_once()

    def test_marks_accepted_for_existing_user(self):
        inv, raw = _make_pending_inv()
        existing_user = _make_user(inv.email)
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_email.return_value = existing_user
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, membership_repo=membership_repo)
        uc.execute(raw_token=raw, name="Alice", phone=PHONE, code=CODE)

        inv_repo.save.assert_called_once()
        saved = inv_repo.save.call_args[0][0]
        assert saved.status == InvitationStatus.ACCEPTED


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestAcceptErrors:
    def test_invalid_token_raises(self):
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = None

        uc = _make_uc(inv_repo=inv_repo)
        with pytest.raises(InvalidInvitationTokenError):
            uc.execute(raw_token="bad-token", name="Alice", phone=PHONE, code=CODE)

    def test_already_accepted_raises(self):
        inv, raw = _make_pending_inv()
        from dataclasses import replace

        accepted_inv = replace(inv, status=InvitationStatus.ACCEPTED)
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = accepted_inv
        user_repo = MagicMock()
        user_repo.find_by_email.return_value = None
        user_repo.save.return_value = _make_user()
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, membership_repo=membership_repo)
        with pytest.raises(InvitationAlreadyAcceptedError):
            uc.execute(raw_token=raw, name="Alice", phone=PHONE, code=CODE)

    def test_expired_invitation_raises(self):
        inv, raw = _make_expired_inv()
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_email.return_value = None
        user_repo.save.return_value = _make_user()
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, membership_repo=membership_repo)
        with pytest.raises(InvitationExpiredError):
            uc.execute(raw_token=raw, name="Alice", phone=PHONE, code=CODE)

    def test_invalid_phone_raises(self):
        """The French-numbers-only rule (BE#172) applies to invitation acceptance too."""
        inv, raw = _make_pending_inv()
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv

        uc = _make_uc(inv_repo=inv_repo)
        with pytest.raises(InvalidPhoneNumberError):
            uc.execute(raw_token=raw, name="Alice", phone="+84912345678", code=CODE)

    def test_blank_name_raises_value_error(self):
        inv, raw = _make_pending_inv()
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv

        uc = _make_uc(inv_repo=inv_repo)
        with pytest.raises(ValueError):
            uc.execute(raw_token=raw, name="   ", phone=PHONE, code=CODE)

    def test_wrong_code_raises_otp_invalid(self):
        inv, raw = _make_pending_inv()
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv

        uc = _make_uc(inv_repo=inv_repo, otp_repo=_make_otp_repo(PHONE, CODE))
        with pytest.raises(OtpInvalidError):
            uc.execute(raw_token=raw, name="Alice", phone=PHONE, code="000000")

    def test_missing_otp_repo_raises_runtime_error(self):
        """execute() must fail loudly, not silently skip phone verification.

        Constructs AcceptInvitationUseCase directly (not via _make_uc(), which
        always injects a working otp_repo) so otp_repo=None is exercised for real.
        """
        inv, raw = _make_pending_inv()
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv

        uc = AcceptInvitationUseCase(
            invitation_repo=inv_repo,
            user_repo=MagicMock(),
            project_membership_repo=MagicMock(),
            token_issuer=MagicMock(),
            db_session=_FakeSession(),
            otp_repo=None,
        )
        with pytest.raises(RuntimeError):
            uc.execute(raw_token=raw, name="Alice", phone=PHONE, code=CODE)

    def test_duplicate_phone_for_new_user_raises(self):
        """The phone is already someone else's account: refused even for a brand-new invitee."""
        inv, raw = _make_pending_inv()
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_email.return_value = None  # new user by email
        user_repo.find_by_phone.return_value = _make_user("someone.else@example.com")  # taken by phone
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, membership_repo=membership_repo)
        with pytest.raises(PhoneAlreadyRegisteredError):
            uc.execute(raw_token=raw, name="Alice", phone=PHONE, code=CODE)

        user_repo.save.assert_not_called()


# ---------------------------------------------------------------------------
# Transactional atomicity
# ---------------------------------------------------------------------------


class TestTransactionAtomicity:
    def test_membership_failure_leaves_invitation_not_accepted(self):
        """If membership_repo.add raises, invitation must NOT be saved as accepted."""
        inv, raw = _make_pending_inv()
        new_user = _make_user(inv.email)
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_email.return_value = None
        user_repo.save.return_value = new_user
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False
        membership_repo.add.side_effect = RuntimeError("DB constraint violation")

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, membership_repo=membership_repo)

        with pytest.raises(RuntimeError):
            uc.execute(raw_token=raw, name="Alice", phone=PHONE, code=CODE)

        # invitation.save must NOT have been called (no accepted status persisted)
        inv_repo.save.assert_not_called()

    def test_uses_locked_lookup_for_the_mutating_accept(self):
        """M1 — the mutating acceptance always resolves the invitation through
        find_by_token_hash_for_update (row lock), so two concurrent accepts of the
        same token serialize at the DB layer instead of racing.

        A separate, non-mutating find_by_token_hash "peek" runs first (fail fast on
        a dead/expired/revoked/already-used token before spending an OTP attempt —
        its result is discarded, nothing is persisted by it), so that call IS
        expected now; what must still hold is that the real accept/mutate decision
        comes from the locked lookup, never the unlocked one.
        """
        inv, raw = _make_pending_inv()
        new_user = _make_user(inv.email)
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash.return_value = inv  # early, non-mutating peek
        inv_repo.find_by_token_hash_for_update.return_value = inv  # the real, locked lookup
        user_repo = MagicMock()
        user_repo.find_by_email.return_value = None
        user_repo.save.return_value = new_user
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, membership_repo=membership_repo)
        uc.execute(raw_token=raw, name="Alice", phone=PHONE, code=CODE)

        inv_repo.find_by_token_hash_for_update.assert_called_once()
