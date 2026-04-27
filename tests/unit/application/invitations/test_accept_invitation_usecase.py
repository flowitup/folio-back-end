"""Unit tests for AcceptInvitationUseCase."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.invitations.accept_invitation_usecase import AcceptInvitationUseCase
from app.domain.entities.invitation import Invitation, InvitationStatus
from app.domain.entities.user import User
from app.domain.exceptions.invitation_exceptions import (
    InvitationAlreadyAcceptedError,
    InvitationExpiredError,
    InvalidInvitationTokenError,
)
from app.domain.value_objects.invite_token import generate_token

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pending_inv() -> tuple[Invitation, str]:
    raw, token_hash = generate_token()
    now = datetime.now(timezone.utc)
    inv = Invitation(
        id=uuid4(),
        email="invitee@example.com",
        project_id=uuid4(),
        role_id=uuid4(),
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
        role_id=uuid4(),
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


def _make_uc(
    inv_repo=None,
    user_repo=None,
    membership_repo=None,
    password_hasher=None,
    token_issuer=None,
    db_session=None,
) -> AcceptInvitationUseCase:
    hasher = password_hasher or MagicMock()
    hasher.hash.return_value = "hashed_password"
    issuer = token_issuer or MagicMock()
    issuer.create_access_token.return_value = "access-jwt"
    issuer.create_refresh_token.return_value = "refresh-jwt"
    return AcceptInvitationUseCase(
        invitation_repo=inv_repo or MagicMock(),
        user_repo=user_repo or MagicMock(),
        project_membership_repo=membership_repo or MagicMock(),
        password_hasher=hasher,
        token_issuer=issuer,
        db_session=db_session or _FakeSession(),
    )


def _make_user(email: str = "invitee@example.com") -> User:
    return User(
        id=uuid4(),
        email=email,
        password_hash="hashed",
        is_active=True,
        created_at=datetime.now(timezone.utc),
        roles=[],
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
        uc.execute(raw_token=raw, name="Alice", password="password123")

        user_repo.save.assert_called_once()
        saved_user_arg = user_repo.save.call_args[0][0]
        assert saved_user_arg.display_name == "Alice"

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
        uc.execute(raw_token=raw, name="Alice", password="password123")

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
        uc.execute(raw_token=raw, name="Alice", password="password123")

        inv_repo.save.assert_called_once()
        saved_inv = inv_repo.save.call_args[0][0]
        assert saved_inv.status == InvitationStatus.ACCEPTED

    def test_returns_auth_tokens(self):
        inv, raw = _make_pending_inv()
        new_user = _make_user(inv.email)
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_email.return_value = None
        user_repo.save.return_value = new_user
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
        result = uc.execute(raw_token=raw, name="Alice", password="password123")

        assert result.access_token == "access-jwt"
        assert result.refresh_token == "refresh-jwt"


# ---------------------------------------------------------------------------
# Race condition: existing user with same email
# ---------------------------------------------------------------------------


class TestExistingUserRace:
    def test_reuses_existing_user_without_changing_password(self):
        inv, raw = _make_pending_inv()
        existing_user = _make_user(inv.email)
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_email.return_value = existing_user  # already exists
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, membership_repo=membership_repo)
        result = uc.execute(raw_token=raw, name="Alice", password="newpassword123")

        # Must NOT call user_repo.save (no user creation)
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
        uc.execute(raw_token=raw, name="Alice", password="newpassword123")

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
        uc.execute(raw_token=raw, name="Alice", password="newpassword123")

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
            uc.execute(raw_token="bad-token", name="Alice", password="password123")

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
            uc.execute(raw_token=raw, name="Alice", password="password123")

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
            uc.execute(raw_token=raw, name="Alice", password="password123")

    def test_short_password_raises_value_error(self):
        inv, raw = _make_pending_inv()
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv

        uc = _make_uc(inv_repo=inv_repo)
        with pytest.raises(ValueError):
            uc.execute(raw_token=raw, name="Alice", password="short")

    def test_blank_name_raises_value_error(self):
        inv, raw = _make_pending_inv()
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv

        uc = _make_uc(inv_repo=inv_repo)
        with pytest.raises(ValueError):
            uc.execute(raw_token=raw, name="   ", password="password123")


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
            uc.execute(raw_token=raw, name="Alice", password="password123")

        # invitation.save must NOT have been called (no accepted status persisted)
        inv_repo.save.assert_not_called()

    def test_uses_locked_lookup_for_concurrent_accept_safety(self):
        """M1 — accept must use find_by_token_hash_for_update (row lock), not find_by_token_hash."""
        inv, raw = _make_pending_inv()
        new_user = _make_user(inv.email)
        inv_repo = MagicMock()
        inv_repo.find_by_token_hash_for_update.return_value = inv
        # If the use-case wrongly fell back to the unlocked variant, this would be called.
        inv_repo.find_by_token_hash.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_email.return_value = None
        user_repo.save.return_value = new_user
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, membership_repo=membership_repo)
        uc.execute(raw_token=raw, name="Alice", password="password123")

        inv_repo.find_by_token_hash_for_update.assert_called_once()
        inv_repo.find_by_token_hash.assert_not_called()
