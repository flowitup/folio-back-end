"""Unit tests for RevokeInvitationUseCase."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.invitations.revoke_invitation_usecase import RevokeInvitationUseCase
from app.application.invitations.exceptions import PermissionDeniedError
from app.domain.entities.invitation import Invitation, InvitationStatus
from app.domain.entities.permission import Permission
from app.domain.entities.role import Role
from app.domain.entities.user import User
from app.domain.exceptions.invitation_exceptions import InvitationNotFoundError
from app.domain.value_objects.invite_token import generate_token


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
        role_id=uuid4(),
        token_hash=token_hash,
        status=status,
        expires_at=now + timedelta(days=7),
        invited_by=inviter_id or uuid4(),
        created_at=now,
        updated_at=now,
    )


def _make_user_with_invite_perm() -> User:
    user = User(
        id=uuid4(), email="admin@example.com", password_hash="h",
        is_active=True, created_at=datetime.now(timezone.utc), roles=[],
    )
    role = Role(id=uuid4(), name="member")
    perm = Permission(id=uuid4(), name="project:invite", resource="project", action="invite")
    role.permissions.append(perm)
    user.roles.append(role)
    return user


def _make_plain_user() -> User:
    return User(
        id=uuid4(), email="plain@example.com", password_hash="h",
        is_active=True, created_at=datetime.now(timezone.utc), roles=[],
    )


def _make_uc(inv_repo=None, user_repo=None) -> RevokeInvitationUseCase:
    return RevokeInvitationUseCase(
        invitation_repo=inv_repo or MagicMock(),
        user_repo=user_repo or MagicMock(),
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

        uc = _make_uc(inv_repo=inv_repo, user_repo=user_repo)
        uc.execute(inviter_id=actor.id, invitation_id=inv.id)

        inv_repo.save.assert_called_once()
        saved = inv_repo.save.call_args[0][0]
        assert saved.status == InvitationStatus.REVOKED

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
