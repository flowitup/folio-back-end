"""Unit tests for ListInvitationsUseCase."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.invitations.list_invitations_usecase import ListInvitationsUseCase
from app.application.invitations.exceptions import PermissionDeniedError
from app.domain.entities.invitation import Invitation, InvitationStatus
from app.domain.entities.user import User
from app.domain.value_objects.invite_token import generate_token
from tests.authz_reader_fake import FakeAuthzReader

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


def _make_user(email="member@example.com") -> User:
    return User(
        id=uuid4(),
        email=email,
        password_hash="h",
        is_active=True,
        created_at=datetime.now(timezone.utc),
        roles=[],
    )


def _reader(project_id, *, role="manager", assigned=True, ops=False) -> FakeAuthzReader:
    """Reader answering `project:invite` for one project (see the matrix)."""
    return FakeAuthzReader(role=role, company_id=uuid4(), project_id=project_id, assigned=assigned, ops=ops)


def _make_uc(inv_repo=None, role_repo=None, user_repo=None, authz_reader=None) -> ListInvitationsUseCase:
    return ListInvitationsUseCase(
        invitation_repo=inv_repo or MagicMock(),
        role_repo=role_repo or MagicMock(),
        user_repo=user_repo or MagicMock(),
        authz_reader=authz_reader,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListInvitations:
    def test_returns_dtos_with_safe_fields_only(self):
        requester = _make_user()
        project_id = uuid4()
        role_id = uuid4()
        inviter_id = uuid4()
        inv = _make_inv(role_id=role_id, inviter_id=inviter_id)

        inv_repo = MagicMock()
        inv_repo.list_by_project.return_value = [inv]
        role_repo = MagicMock()
        mock_role = MagicMock()
        mock_role.name = "member"
        role_repo.find_by_id.return_value = mock_role
        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: (
            requester if uid == requester.id else MagicMock(display_or_email="Inviter")
        )

        uc = _make_uc(
            inv_repo=inv_repo,
            role_repo=role_repo,
            user_repo=user_repo,
            authz_reader=_reader(project_id),
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
        requester = _make_user()
        project_id = uuid4()
        inv_accepted = _make_inv(InvitationStatus.ACCEPTED)

        inv_repo = MagicMock()
        inv_repo.list_by_project.return_value = [inv_accepted]  # filtered by repo
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = MagicMock(name="member")
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = requester

        uc = _make_uc(
            inv_repo=inv_repo,
            role_repo=role_repo,
            user_repo=user_repo,
            authz_reader=_reader(project_id),
        )
        uc.execute(
            requester_id=requester.id,
            project_id=project_id,
            status_filter="accepted",
        )

        inv_repo.list_by_project.assert_called_once_with(project_id, status=InvitationStatus.ACCEPTED)

    def test_member_without_invite_permission_is_denied(self):
        """Being on the project is not enough — listing invitations is `project:invite`."""
        requester = _make_user()
        project_id = uuid4()

        user_repo = MagicMock()
        user_repo.find_by_id.return_value = requester

        uc = _make_uc(user_repo=user_repo, authz_reader=_reader(project_id, role="member"))
        with pytest.raises(PermissionDeniedError):
            uc.execute(requester_id=requester.id, project_id=project_id)

    def test_manager_of_another_project_is_denied(self):
        """A manager's `project:invite` does not reach a project they are not assigned to."""
        requester = _make_user()

        user_repo = MagicMock()
        user_repo.find_by_id.return_value = requester

        uc = _make_uc(user_repo=user_repo, authz_reader=_reader(uuid4()))
        with pytest.raises(PermissionDeniedError):
            uc.execute(requester_id=requester.id, project_id=uuid4())

    def test_without_a_reader_the_check_fails_closed(self):
        requester = _make_user()

        user_repo = MagicMock()
        user_repo.find_by_id.return_value = requester

        uc = _make_uc(user_repo=user_repo, authz_reader=None)
        with pytest.raises(PermissionDeniedError):
            uc.execute(requester_id=requester.id, project_id=uuid4())

    def test_platform_ops_can_list_without_a_company_role(self):
        ops = _make_user(email="ops@example.com")
        project_id = uuid4()

        inv_repo = MagicMock()
        inv_repo.list_by_project.return_value = []
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = ops

        uc = _make_uc(
            inv_repo=inv_repo,
            user_repo=user_repo,
            authz_reader=_reader(project_id, role="member", assigned=False, ops=True),
        )
        assert uc.execute(requester_id=ops.id, project_id=project_id) == []

    def test_company_admin_lists_without_an_assignment(self):
        admin = _make_user(email="admin@example.com")
        project_id = uuid4()

        inv_repo = MagicMock()
        inv_repo.list_by_project.return_value = []
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = admin

        uc = _make_uc(
            inv_repo=inv_repo,
            user_repo=user_repo,
            authz_reader=_reader(project_id, role="admin", assigned=False),
        )
        assert uc.execute(requester_id=admin.id, project_id=project_id) == []

    def test_unknown_status_filter_returns_empty_list(self):
        requester = _make_user()
        project_id = uuid4()

        user_repo = MagicMock()
        user_repo.find_by_id.return_value = requester

        uc = _make_uc(user_repo=user_repo, authz_reader=_reader(project_id))
        result = uc.execute(
            requester_id=requester.id,
            project_id=project_id,
            status_filter="invalid_status",
        )
        assert result == []

    def test_returns_multiple_items(self):
        requester = _make_user()
        project_id = uuid4()
        invs = [_make_inv() for _ in range(3)]

        inv_repo = MagicMock()
        inv_repo.list_by_project.return_value = invs
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = MagicMock(name="member")
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = requester

        uc = _make_uc(
            inv_repo=inv_repo,
            role_repo=role_repo,
            user_repo=user_repo,
            authz_reader=_reader(project_id),
        )
        result = uc.execute(requester_id=requester.id, project_id=project_id)
        assert len(result) == 3
