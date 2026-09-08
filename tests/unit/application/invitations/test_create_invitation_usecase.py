"""Unit tests for CreateInvitationUseCase — mocked repositories."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.invitations.create_invitation_usecase import CreateInvitationUseCase
from app.application.invitations.exceptions import (
    PermissionDeniedError,
    RateLimitedError,
    ProjectNotFoundError,
)
from app.domain.entities.invitation import Invitation, InvitationStatus
from app.domain.entities.project import Project
from app.domain.entities.user import User

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _InviteReader:
    """AuthzReaderPort double: `allow` decides `project:invite` on every project.

    The permission itself is covered by test_can_invite_resolver.py; these tests
    only need the gate open (or shut) so they can exercise the invite flow.
    """

    def __init__(self, allow: bool = True, company_id=None) -> None:
        self._allow = allow
        self._company_id = company_id

    def company_role_for(self, user_id, company_id):
        return "admin" if self._allow else "member"

    def is_assigned(self, user_id, project_id):
        return True

    def project_company_id(self, project_id):
        return self._company_id or uuid4()

    def grants_for(self, user_id, company_id, project_id):
        return []

    def primary_company_id(self, user_id):
        return None

    def admin_company_ids(self, user_id):
        return []

    def company_roles_for(self, user_id):
        return []

    def is_platform_ops(self, user_id):
        return False

    def has_project_assignment_in_company(self, user_id, company_id):
        return True

    def project_ids_for_company(self, company_id):
        return []

    def assigned_project_ids(self, user_id, project_ids):
        return []

    def assigned_project_ids_for_users(self, company_id, user_ids):
        return {}


def _make_user(*, has_invite_perm: bool = False, is_superadmin: bool = False) -> User:
    """Identity only — what the inviter may do comes from the reader, not the user row."""
    return User(
        id=uuid4(),
        email="inviter@example.com",
        password_hash="hashed",
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )


def _make_project(owner_id=None) -> Project:
    return Project(
        id=uuid4(),
        name="Test Project",
        owner_id=owner_id or uuid4(),
        created_at=datetime.now(timezone.utc),
    )


class _FakeSession:
    """Test fake for the TransactionalSessionPort — counts commit() calls."""

    def __init__(self) -> None:
        self.commit_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1

    def begin_nested(self):
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            yield self

        return _ctx()


def _make_usecase(
    inv_repo=None,
    membership_repo=None,
    user_repo=None,
    project_repo=None,
    email_port=None,
    queue_port=None,
    db_session=None,
    may_invite: bool = True,
    access_repo=None,
    company_id=None,
) -> CreateInvitationUseCase:
    renderer = MagicMock()
    renderer.render.return_value = ("Subject", "Text body", "<html>body</html>")
    return CreateInvitationUseCase(
        invitation_repo=inv_repo or MagicMock(),
        project_membership_repo=membership_repo or MagicMock(),
        user_repo=user_repo or MagicMock(),
        project_repo=project_repo or MagicMock(),
        email_port=email_port or MagicMock(),
        email_renderer=renderer,
        queue_port=queue_port or MagicMock(),
        app_base_url="http://localhost:3000",
        db_session=db_session or _FakeSession(),
        authz_reader=_InviteReader(may_invite, company_id),
        access_repo=access_repo,
    )


# ---------------------------------------------------------------------------
# Happy path: new email → invitation_sent
# ---------------------------------------------------------------------------


class TestNewEmailPath:
    def test_returns_invitation_sent_kind(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        inv_repo = MagicMock()
        inv_repo.find_pending_by_email_and_project.return_value = None
        inv_repo.count_created_today_by_project.return_value = 0
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = None
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        queue = MagicMock()

        uc = _make_usecase(
            inv_repo=inv_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            queue_port=queue,
        )
        result = uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="newuser@example.com",
        )

        assert result.kind == "invitation_sent"
        assert result.invitation_id is not None
        assert result.expires_at is not None

    def test_repo_save_called(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        inv_repo = MagicMock()
        inv_repo.find_pending_by_email_and_project.return_value = None
        inv_repo.count_created_today_by_project.return_value = 0
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = None
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        uc = _make_usecase(
            inv_repo=inv_repo,
            user_repo=user_repo,
            project_repo=project_repo,
        )
        uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="newuser@example.com",
        )

        inv_repo.save.assert_called_once()

    def test_queue_enqueue_called_with_invite_template(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        inv_repo = MagicMock()
        inv_repo.find_pending_by_email_and_project.return_value = None
        inv_repo.count_created_today_by_project.return_value = 0
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = None
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        queue = MagicMock()
        renderer = MagicMock()
        renderer.render.return_value = ("Subject", "Body", "<html/>")

        uc = CreateInvitationUseCase(
            invitation_repo=inv_repo,
            project_membership_repo=MagicMock(),
            user_repo=user_repo,
            project_repo=project_repo,
            email_port=MagicMock(),
            email_renderer=renderer,
            queue_port=queue,
            app_base_url="http://localhost:3000",
            db_session=_FakeSession(),
            authz_reader=_InviteReader(),
        )
        uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="newuser@example.com",
        )

        queue.enqueue.assert_called_once()
        call_args = queue.enqueue.call_args
        assert call_args[0][0] == "tasks.send_email"
        # Renderer should have been called with the 'invite' template
        renderer.render.assert_called_once()
        assert renderer.render.call_args[0][0] == "invite"


# ---------------------------------------------------------------------------
# Happy path: existing user → direct_added
# ---------------------------------------------------------------------------


class TestExistingUserPath:
    def test_returns_direct_added_kind(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        existing_user = User(
            id=uuid4(),
            email="existing@example.com",
            password_hash="hashed",
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = existing_user
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        uc = _make_usecase(
            membership_repo=membership_repo,
            user_repo=user_repo,
            project_repo=project_repo,
        )
        result = uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="existing@example.com",
        )

        assert result.kind == "direct_added"
        assert result.user_id == existing_user.id

    def test_no_invitation_token_for_existing_user(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        existing_user = User(
            id=uuid4(),
            email="existing@example.com",
            password_hash="hashed",
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = existing_user
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        uc = _make_usecase(
            membership_repo=membership_repo,
            user_repo=user_repo,
            project_repo=project_repo,
        )
        result = uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="existing@example.com",
        )

        assert result.invitation_id is None

    def test_added_to_project_email_enqueued_not_invite_template(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        existing_user = User(
            id=uuid4(),
            email="existing@example.com",
            password_hash="hashed",
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = existing_user
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        renderer = MagicMock()
        renderer.render.return_value = ("Subj", "Body", "<html/>")
        queue = MagicMock()

        uc = CreateInvitationUseCase(
            invitation_repo=MagicMock(),
            project_membership_repo=membership_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            email_port=MagicMock(),
            email_renderer=renderer,
            queue_port=queue,
            app_base_url="http://localhost:3000",
            db_session=_FakeSession(),
            authz_reader=_InviteReader(),
        )
        uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="existing@example.com",
        )

        renderer.render.assert_called_once()
        assert renderer.render.call_args[0][0] == "added_to_project"

    def test_existing_assignment_is_an_idempotent_noop(self):
        """Already assigned → direct_added, no second email (H2)."""
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        existing_user = User(
            id=uuid4(),
            email="member@example.com",
            password_hash="hashed",
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        membership_repo = MagicMock()
        membership_repo.exists.return_value = True
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = existing_user
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        renderer = MagicMock()
        queue = MagicMock()

        uc = CreateInvitationUseCase(
            invitation_repo=MagicMock(),
            project_membership_repo=membership_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            email_port=MagicMock(),
            email_renderer=renderer,
            queue_port=queue,
            app_base_url="http://localhost:3000",
            db_session=_FakeSession(),
            authz_reader=_InviteReader(),
        )
        result = uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="member@example.com",
        )

        assert result.kind == "direct_added"
        # No new membership added, no email enqueued
        membership_repo.add.assert_not_called()
        queue.enqueue.assert_not_called()
        renderer.render.assert_not_called()


# ---------------------------------------------------------------------------
# Permission checks
# ---------------------------------------------------------------------------


class TestPermissionChecks:
    def test_inviter_without_perm_and_not_owner_raises(self):
        inviter = _make_user(has_invite_perm=False)
        project = _make_project(owner_id=uuid4())  # different owner
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        uc = _make_usecase(user_repo=user_repo, project_repo=project_repo, may_invite=False)

        with pytest.raises(PermissionDeniedError):
            uc.execute(
                inviter_id=inviter.id,
                project_id=project.id,
                email="someone@example.com",
            )

    def test_inviter_is_owner_without_the_permission_is_refused(self):
        """D6: owning the project is not a permission — the resolver decides."""
        inviter = _make_user(has_invite_perm=False)
        project = _make_project(owner_id=inviter.id)  # same owner
        inv_repo = MagicMock()
        inv_repo.find_pending_by_email_and_project.return_value = None
        inv_repo.count_created_today_by_project.return_value = 0
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = None
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        uc = _make_usecase(
            inv_repo=inv_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            may_invite=False,
        )
        with pytest.raises(PermissionDeniedError):
            uc.execute(
                inviter_id=inviter.id,
                project_id=project.id,
                email="newuser@example.com",
            )

    def test_company_admin_can_invite(self):
        inviter = _make_user(is_superadmin=True)
        project = _make_project(owner_id=uuid4())
        inv_repo = MagicMock()
        inv_repo.find_pending_by_email_and_project.return_value = None
        inv_repo.count_created_today_by_project.return_value = 0
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = None
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        uc = _make_usecase(
            inv_repo=inv_repo,
            user_repo=user_repo,
            project_repo=project_repo,
        )
        result = uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="newuser@example.com",
        )
        assert result.kind == "invitation_sent"


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_per_project_50_daily_cap_raises(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        inv_repo = MagicMock()
        inv_repo.count_created_today_by_project.return_value = 50  # at cap
        inv_repo.find_pending_by_email_and_project.return_value = None
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = None
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        uc = _make_usecase(
            inv_repo=inv_repo,
            user_repo=user_repo,
            project_repo=project_repo,
        )
        with pytest.raises(RateLimitedError):
            uc.execute(
                inviter_id=inviter.id,
                project_id=project.id,
                email="another@example.com",
            )


# ---------------------------------------------------------------------------
# Duplicate pending → revoke old, create new
# ---------------------------------------------------------------------------


class TestDuplicatePending:
    def test_revokes_old_pending_then_creates_new(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        existing_inv, _ = Invitation.create(
            email="dup@example.com",
            project_id=project.id,
            invited_by=inviter.id,
        )
        inv_repo = MagicMock()
        inv_repo.find_pending_by_email_and_project.return_value = existing_inv
        inv_repo.count_created_today_by_project.return_value = 0
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = None
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        uc = _make_usecase(
            inv_repo=inv_repo,
            user_repo=user_repo,
            project_repo=project_repo,
        )
        uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="dup@example.com",
        )

        # save should be called twice: once for revoke, once for new
        assert inv_repo.save.call_count == 2
        # First save was the revoked one
        first_saved = inv_repo.save.call_args_list[0][0][0]
        assert first_saved.status == InvitationStatus.REVOKED


# ---------------------------------------------------------------------------
# Missing resources
# ---------------------------------------------------------------------------


class TestMissingResources:
    def test_project_not_found_raises(self):
        inviter = _make_user(has_invite_perm=True)
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = None

        uc = _make_usecase(user_repo=user_repo, project_repo=project_repo)
        with pytest.raises(ProjectNotFoundError):
            uc.execute(
                inviter_id=inviter.id,
                project_id=uuid4(),
                email="user@example.com",
            )


# ---------------------------------------------------------------------------
# Existing user → company attachment (permissions resolve through the company)
# ---------------------------------------------------------------------------


class TestDirectAddAttachesToTheProjectCompany:
    def _run(self, access_repo, company_id):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        existing_user = User(
            id=uuid4(),
            email="existing@example.com",
            password_hash="hashed",
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = existing_user
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project

        uc = _make_usecase(
            membership_repo=membership_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            access_repo=access_repo,
            company_id=company_id,
        )
        result = uc.execute(inviter_id=inviter.id, project_id=project.id, email="existing@example.com")
        return result, existing_user

    def test_an_unattached_user_becomes_a_company_member(self):
        company_id = uuid4()
        access_repo = MagicMock()
        access_repo.find.return_value = None
        access_repo.list_for_user.return_value = []

        result, existing_user = self._run(access_repo, company_id)

        assert result.kind == "direct_added"
        access_repo.save.assert_called_once()
        saved = access_repo.save.call_args[0][0]
        assert saved.user_id == existing_user.id
        assert saved.company_id == company_id
        assert saved.role == "member"
        assert saved.is_primary is True  # their first company

    def test_an_already_attached_user_keeps_their_role(self):
        access_repo = MagicMock()
        access_repo.find.return_value = object()  # any existing access row

        result, _ = self._run(access_repo, uuid4())

        assert result.kind == "direct_added"
        access_repo.save.assert_not_called()
