"""Unit tests for ListInvitationsUseCase."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.invitations.list_invitations_usecase import ListInvitationsUseCase
from app.application.invitations.exceptions import PermissionDeniedError
from app.domain.entities.invitation import Invitation, InvitationStatus
from app.domain.entities.permission import Permission
from app.domain.entities.role import Role
from app.domain.entities.user import User
from app.domain.value_objects.invite_token import generate_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inv(status: InvitationStatus = InvitationStatus.PENDING, role_id=None, inviter_id=None) -> Invitation:
    _, token_hash = generate_token()
    now = datetime.now(timezone.utc)
    return Invitation(
        id=uuid4(),
        email="user@example.com",
        project_id=uuid4(),
        role_id=role_id or uuid4(),
        token_hash=token_hash,
        status=status,
        expires_at=now + timedelta(days=7),
        invited_by=inviter_id or uuid4(),
        created_at=now,
        updated_at=now,
    )


def _make_member_user() -> User:
    user = User(
        id=uuid4(), email="member@example.com", password_hash="h",
        is_active=True, created_at=datetime.now(timezone.utc), roles=[],
    )
    return user


def _make_superadmin_user() -> User:
    user = User(
        id=uuid4(), email="sa@example.com", password_hash="h",
        is_active=True, created_at=datetime.now(timezone.utc), roles=[],
    )
    role = Role(id=uuid4(), name="superadmin")
    perm = Permission(id=uuid4(), name="*:*", resource="*", action="*")
    role.permissions.append(perm)
    user.roles.append(role)
    return user


def _make_uc(inv_repo=None, membership_repo=None, role_repo=None, user_repo=None) -> ListInvitationsUseCase:
    return ListInvitationsUseCase(
        invitation_repo=inv_repo or MagicMock(),
        project_membership_repo=membership_repo or MagicMock(),
        role_repo=role_repo or MagicMock(),
        user_repo=user_repo or MagicMock(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestListInvitations:
    def test_returns_dtos_with_safe_fields_only(self):
        requester = _make_member_user()
        project_id = uuid4()
        role_id = uuid4()
        inviter_id = uuid4()
        inv = _make_inv(role_id=role_id, inviter_id=inviter_id)

        inv_repo = MagicMock()
        inv_repo.list_by_project.return_value = [inv]
        membership_repo = MagicMock()
        membership_repo.exists.return_value = True
        role_repo = MagicMock()
        mock_role = MagicMock()
        mock_role.name = "member"
        role_repo.find_by_id.return_value = mock_role
        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: (
            requester if uid == requester.id
            else MagicMock(display_or_email="Inviter")
        )

        uc = _make_uc(
            inv_repo=inv_repo,
            membership_repo=membership_repo,
            role_repo=role_repo,
            user_repo=user_repo,
        )
        result = uc.execute(requester_id=requester.id, project_id=project_id)

        assert len(result) == 1
        item = result[0]
        assert item.email == inv.email
        assert item.role_name == "member"
        assert item.status == inv.status
        assert item.expires_at == inv.expires_at
        assert item.created_at == inv.created_at
        # DTO must NOT expose token_hash
        assert not hasattr(item, "token_hash")

    def test_filters_by_status(self):
        requester = _make_member_user()
        project_id = uuid4()
        inv_pending = _make_inv(InvitationStatus.PENDING)
        inv_accepted = _make_inv(InvitationStatus.ACCEPTED)

        inv_repo = MagicMock()
        inv_repo.list_by_project.return_value = [inv_accepted]  # filtered by repo
        membership_repo = MagicMock()
        membership_repo.exists.return_value = True
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = MagicMock(name="member")
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = requester

        uc = _make_uc(
            inv_repo=inv_repo,
            membership_repo=membership_repo,
            role_repo=role_repo,
            user_repo=user_repo,
        )
        result = uc.execute(
            requester_id=requester.id,
            project_id=project_id,
            status_filter="accepted",
        )

        inv_repo.list_by_project.assert_called_once_with(project_id, status=InvitationStatus.ACCEPTED)

    def test_non_member_raises_permission_denied(self):
        requester = _make_member_user()
        project_id = uuid4()

        membership_repo = MagicMock()
        membership_repo.exists.return_value = False  # not a member
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = requester

        uc = _make_uc(membership_repo=membership_repo, user_repo=user_repo)
        with pytest.raises(PermissionDeniedError):
            uc.execute(requester_id=requester.id, project_id=project_id)

    def test_superadmin_can_list_without_membership(self):
        superadmin = _make_superadmin_user()
        project_id = uuid4()

        inv_repo = MagicMock()
        inv_repo.list_by_project.return_value = []
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False  # not a member
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = superadmin

        uc = _make_uc(
            inv_repo=inv_repo,
            membership_repo=membership_repo,
            user_repo=user_repo,
        )
        result = uc.execute(requester_id=superadmin.id, project_id=project_id)
        assert result == []

    def test_unknown_status_filter_returns_empty_list(self):
        requester = _make_member_user()
        project_id = uuid4()

        membership_repo = MagicMock()
        membership_repo.exists.return_value = True
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = requester

        uc = _make_uc(membership_repo=membership_repo, user_repo=user_repo)
        result = uc.execute(
            requester_id=requester.id,
            project_id=project_id,
            status_filter="invalid_status",
        )
        assert result == []

    def test_returns_multiple_items(self):
        requester = _make_member_user()
        project_id = uuid4()
        invs = [_make_inv() for _ in range(3)]

        inv_repo = MagicMock()
        inv_repo.list_by_project.return_value = invs
        membership_repo = MagicMock()
        membership_repo.exists.return_value = True
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = MagicMock(name="member")
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = requester

        uc = _make_uc(
            inv_repo=inv_repo,
            membership_repo=membership_repo,
            role_repo=role_repo,
            user_repo=user_repo,
        )
        result = uc.execute(requester_id=requester.id, project_id=project_id)
        assert len(result) == 3
