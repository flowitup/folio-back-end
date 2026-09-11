"""Unit tests for RevokeInvitationUseCase."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.invitations.revoke_invitation_usecase import RevokeInvitationUseCase
from app.application.invitations.exceptions import PermissionDeniedError
from app.domain.entities.invitation import Invitation, InvitationStatus
from app.domain.entities.user import User
from app.domain.exceptions.invitation_exceptions import InvitationNotFoundError
from app.domain.value_objects.invite_token import generate_token
from tests.authz_reader_fake import FakeAuthzReader

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_inv(status: InvitationStatus = InvitationStatus.PENDING, inviter_id=None) -> Invitation:
    _, token_hash = generate_token()
    now = datetime.now(timezone.utc)
    return Invitation(
        id=uuid4(),
        email="user@example.com",
        project_id=uuid4(),
        token_hash=token_hash,
        status=status,
        expires_at=now + timedelta(days=7),
        invited_by=inviter_id or uuid4(),
        created_at=now,
        updated_at=now,
    )


def _make_user(email="admin@example.com") -> User:
    return User(id=uuid4(), email=email, is_active=True, created_at=datetime.now(timezone.utc))


# The legacy helper names read better as intent: both build a plain user now —
# what a caller may do comes from the resolver, not from a role row.
_make_user_with_invite_perm = _make_user
_make_plain_user = _make_user


def _reader(project_id, *, role="manager", assigned=True, ops=False) -> FakeAuthzReader:
    return FakeAuthzReader(role=role, company_id=uuid4(), project_id=project_id, assigned=assigned, ops=ops)


def _make_uc(inv_repo=None, user_repo=None, db_session=None, authz_reader=None) -> RevokeInvitationUseCase:
    return RevokeInvitationUseCase(
        invitation_repo=inv_repo or MagicMock(),
        user_repo=user_repo or MagicMock(),
        db_session=db_session or MagicMock(),
        authz_reader=authz_reader,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRevokeInvitation:
    def test_pending_becomes_revoked(self):
        actor = _make_user_with_invite_perm()
        inv = _make_inv(InvitationStatus.PENDING, inviter_id=actor.id)

        inv_repo = MagicMock()
        inv_repo.find_by_id.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = actor
        db = MagicMock()

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, db_session=db)
        uc.execute(inviter_id=actor.id, invitation_id=inv.id)

        inv_repo.save.assert_called_once()
        saved = inv_repo.save.call_args[0][0]
        assert saved.status == InvitationStatus.REVOKED
        # Commit must follow save — without it Flask-SQLAlchemy rolls back on teardown.
        db.commit.assert_called_once()

    def test_already_revoked_is_idempotent_no_exception(self):
        actor = _make_user_with_invite_perm()
        inv = _make_inv(InvitationStatus.REVOKED, inviter_id=actor.id)

        inv_repo = MagicMock()
        inv_repo.find_by_id.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = actor

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo)
        # Must not raise
        uc.execute(inviter_id=actor.id, invitation_id=inv.id)
        # No save called — already non-pending
        inv_repo.save.assert_not_called()

    def test_accepted_invitation_is_idempotent_no_exception(self):
        actor = _make_user_with_invite_perm()
        inv = _make_inv(InvitationStatus.ACCEPTED, inviter_id=actor.id)

        inv_repo = MagicMock()
        inv_repo.find_by_id.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = actor

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo)
        uc.execute(inviter_id=actor.id, invitation_id=inv.id)
        inv_repo.save.assert_not_called()

    def test_expired_invitation_is_idempotent_no_exception(self):
        actor = _make_user_with_invite_perm()
        inv = _make_inv(InvitationStatus.EXPIRED, inviter_id=actor.id)

        inv_repo = MagicMock()
        inv_repo.find_by_id.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = actor

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo)
        uc.execute(inviter_id=actor.id, invitation_id=inv.id)
        inv_repo.save.assert_not_called()

    def test_not_found_raises(self):
        actor = _make_user_with_invite_perm()
        inv_repo = MagicMock()
        inv_repo.find_by_id.return_value = None
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = actor

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo)
        with pytest.raises(InvitationNotFoundError):
            uc.execute(inviter_id=actor.id, invitation_id=uuid4())

    def test_unauthorized_user_raises_permission_denied(self):
        actor = _make_plain_user()  # no invite permission
        other_inviter_id = uuid4()  # invitation was sent by someone else
        inv = _make_inv(InvitationStatus.PENDING, inviter_id=other_inviter_id)

        inv_repo = MagicMock()
        inv_repo.find_by_id.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = actor

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo)
        with pytest.raises(PermissionDeniedError):
            uc.execute(inviter_id=actor.id, invitation_id=inv.id)

    def test_original_inviter_can_revoke_without_perm(self):
        """The user who sent the invitation can revoke it even without project:invite perm."""
        actor = _make_plain_user()
        inv = _make_inv(InvitationStatus.PENDING, inviter_id=actor.id)  # actor IS the inviter

        inv_repo = MagicMock()
        inv_repo.find_by_id.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = actor

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo)
        # Must not raise
        uc.execute(inviter_id=actor.id, invitation_id=inv.id)
        inv_repo.save.assert_called_once()


class TestRevokeAuthorization:
    """Revoking is `project:invite` on the invitation's own project (C2)."""

    def test_manager_of_the_invitation_project_may_revoke(self):
        actor = _make_user()
        inv = _make_inv(InvitationStatus.PENDING, inviter_id=uuid4())

        inv_repo = MagicMock()
        inv_repo.find_by_id.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = actor

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, authz_reader=_reader(inv.project_id))
        uc.execute(inviter_id=actor.id, invitation_id=inv.id)
        inv_repo.save.assert_called_once()

    def test_manager_of_another_project_may_not_revoke(self):
        """The hole this closes: a global `manager` role reached every project."""
        actor = _make_user()
        inv = _make_inv(InvitationStatus.PENDING, inviter_id=uuid4())

        inv_repo = MagicMock()
        inv_repo.find_by_id.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = actor

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, authz_reader=_reader(uuid4()))
        with pytest.raises(PermissionDeniedError):
            uc.execute(inviter_id=actor.id, invitation_id=inv.id)
        inv_repo.save.assert_not_called()

    def test_member_of_the_project_may_not_revoke(self):
        actor = _make_user()
        inv = _make_inv(InvitationStatus.PENDING, inviter_id=uuid4())

        inv_repo = MagicMock()
        inv_repo.find_by_id.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = actor

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo, authz_reader=_reader(inv.project_id, role="member"))
        with pytest.raises(PermissionDeniedError):
            uc.execute(inviter_id=actor.id, invitation_id=inv.id)

    def test_platform_ops_may_revoke_anything(self):
        actor = _make_user(email="ops@example.com")
        inv = _make_inv(InvitationStatus.PENDING, inviter_id=uuid4())

        inv_repo = MagicMock()
        inv_repo.find_by_id.return_value = inv
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = actor

        uc = _make_uc(
            inv_repo=inv_repo,
            user_repo=user_repo,
            authz_reader=_reader(inv.project_id, role="member", assigned=False, ops=True),
        )
        uc.execute(inviter_id=actor.id, invitation_id=inv.id)
        inv_repo.save.assert_called_once()
