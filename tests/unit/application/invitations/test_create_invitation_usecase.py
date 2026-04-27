"""Unit tests for CreateInvitationUseCase — mocked repositories."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.application.invitations.create_invitation_usecase import CreateInvitationUseCase
from app.application.invitations.exceptions import (
    PermissionDeniedError,
    RateLimitedError,
    RoleNotFoundError,
    ProjectNotFoundError,
)
from app.domain.entities.invitation import Invitation, InvitationStatus
from app.domain.entities.permission import Permission
from app.domain.entities.project import Project
from app.domain.entities.role import Role
from app.domain.entities.user import User
from app.domain.exceptions.invitation_exceptions import RoleNotAllowedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(*, has_invite_perm: bool = False, is_superadmin: bool = False) -> User:
    user = User(
        id=uuid4(),
        email="inviter@example.com",
        password_hash="hashed",
        is_active=True,
        created_at=datetime.now(timezone.utc),
        roles=[],
    )
    if is_superadmin:
        role = Role(id=uuid4(), name="superadmin")
        perm = Permission(id=uuid4(), name="*:*", resource="*", action="*")
        role.permissions.append(perm)
        user.roles.append(role)
    elif has_invite_perm:
        role = Role(id=uuid4(), name="member")
        perm = Permission(id=uuid4(), name="project:invite", resource="project", action="invite")
        role.permissions.append(perm)
        user.roles.append(role)
    return user


def _make_project(owner_id=None) -> Project:
    return Project(
        id=uuid4(),
        name="Test Project",
        owner_id=owner_id or uuid4(),
        created_at=datetime.now(timezone.utc),
    )


def _make_role(name: str = "member") -> Role:
    return Role(id=uuid4(), name=name)


def _make_usecase(
    inv_repo=None,
    membership_repo=None,
    user_repo=None,
    project_repo=None,
    role_repo=None,
    email_port=None,
    queue_port=None,
) -> CreateInvitationUseCase:
    renderer = MagicMock()
    renderer.render.return_value = ("Subject", "Text body", "<html>body</html>")
    return CreateInvitationUseCase(
        invitation_repo=inv_repo or MagicMock(),
        project_membership_repo=membership_repo or MagicMock(),
        user_repo=user_repo or MagicMock(),
        project_repo=project_repo or MagicMock(),
        role_repo=role_repo or MagicMock(),
        email_port=email_port or MagicMock(),
        email_renderer=renderer,
        queue_port=queue_port or MagicMock(),
        app_base_url="http://localhost:3000",
    )


# ---------------------------------------------------------------------------
# Happy path: new email → invitation_sent
# ---------------------------------------------------------------------------

class TestNewEmailPath:
    def test_returns_invitation_sent_kind(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        role = _make_role()
        inv_repo = MagicMock()
        inv_repo.find_pending_by_email_and_project.return_value = None
        inv_repo.count_created_today_by_project.return_value = 0
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = None
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role
        queue = MagicMock()

        uc = _make_usecase(
            inv_repo=inv_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            queue_port=queue,
        )
        result = uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="newuser@example.com",
            role_id=role.id,
        )

        assert result.kind == "invitation_sent"
        assert result.invitation_id is not None
        assert result.expires_at is not None

    def test_repo_save_called(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        role = _make_role()
        inv_repo = MagicMock()
        inv_repo.find_pending_by_email_and_project.return_value = None
        inv_repo.count_created_today_by_project.return_value = 0
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = None
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        uc = _make_usecase(
            inv_repo=inv_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
        )
        uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="newuser@example.com",
            role_id=role.id,
        )

        inv_repo.save.assert_called_once()

    def test_queue_enqueue_called_with_invite_template(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        role = _make_role()
        inv_repo = MagicMock()
        inv_repo.find_pending_by_email_and_project.return_value = None
        inv_repo.count_created_today_by_project.return_value = 0
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = None
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role
        queue = MagicMock()
        renderer = MagicMock()
        renderer.render.return_value = ("Subject", "Body", "<html/>")

        uc = CreateInvitationUseCase(
            invitation_repo=inv_repo,
            project_membership_repo=MagicMock(),
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            email_port=MagicMock(),
            email_renderer=renderer,
            queue_port=queue,
            app_base_url="http://localhost:3000",
        )
        uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="newuser@example.com",
            role_id=role.id,
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
        role = _make_role()
        existing_user = User(
            id=uuid4(),
            email="existing@example.com",
            password_hash="hashed",
            is_active=True,
            created_at=datetime.now(timezone.utc),
            roles=[],
        )
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False
        membership_repo.find_role_id.return_value = None  # not yet a member
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = existing_user
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        uc = _make_usecase(
            membership_repo=membership_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
        )
        result = uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="existing@example.com",
            role_id=role.id,
        )

        assert result.kind == "direct_added"
        assert result.user_id == existing_user.id

    def test_no_invitation_token_for_existing_user(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        role = _make_role()
        existing_user = User(
            id=uuid4(), email="existing@example.com", password_hash="hashed",
            is_active=True, created_at=datetime.now(timezone.utc), roles=[],
        )
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False
        membership_repo.find_role_id.return_value = None  # not yet a member
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = existing_user
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        uc = _make_usecase(
            membership_repo=membership_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
        )
        result = uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="existing@example.com",
            role_id=role.id,
        )

        assert result.invitation_id is None

    def test_added_to_project_email_enqueued_not_invite_template(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        role = _make_role()
        existing_user = User(
            id=uuid4(), email="existing@example.com", password_hash="hashed",
            is_active=True, created_at=datetime.now(timezone.utc), roles=[],
        )
        membership_repo = MagicMock()
        membership_repo.exists.return_value = False
        membership_repo.find_role_id.return_value = None  # not yet a member
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = existing_user
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role
        renderer = MagicMock()
        renderer.render.return_value = ("Subj", "Body", "<html/>")
        queue = MagicMock()

        uc = CreateInvitationUseCase(
            invitation_repo=MagicMock(),
            project_membership_repo=membership_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            email_port=MagicMock(),
            email_renderer=renderer,
            queue_port=queue,
            app_base_url="http://localhost:3000",
        )
        uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="existing@example.com",
            role_id=role.id,
        )

        renderer.render.assert_called_once()
        assert renderer.render.call_args[0][0] == "added_to_project"

    def test_existing_member_same_role_is_idempotent_noop(self):
        """Already a member with SAME role → direct_added, no email enqueued (H2)."""
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        role = _make_role()
        existing_user = User(
            id=uuid4(), email="member@example.com", password_hash="hashed",
            is_active=True, created_at=datetime.now(timezone.utc), roles=[],
        )
        membership_repo = MagicMock()
        # Already a member with the same role_id → idempotent no-op
        membership_repo.find_role_id.return_value = role.id
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = existing_user
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role
        renderer = MagicMock()
        queue = MagicMock()

        uc = CreateInvitationUseCase(
            invitation_repo=MagicMock(),
            project_membership_repo=membership_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            email_port=MagicMock(),
            email_renderer=renderer,
            queue_port=queue,
            app_base_url="http://localhost:3000",
        )
        result = uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="member@example.com",
            role_id=role.id,
        )

        assert result.kind == "direct_added"
        # No new membership added, no email enqueued
        membership_repo.add.assert_not_called()
        queue.enqueue.assert_not_called()
        renderer.render.assert_not_called()

    def test_existing_member_different_role_raises_already_member_error(self):
        """Already a member with DIFFERENT role → AlreadyMemberError (maps to 409, H2)."""
        from app.application.invitations.exceptions import AlreadyMemberError
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        role = _make_role()  # the role admin is *trying* to assign
        existing_user = User(
            id=uuid4(), email="member@example.com", password_hash="hashed",
            is_active=True, created_at=datetime.now(timezone.utc), roles=[],
        )
        # Membership exists but with a *different* role_id
        membership_repo = MagicMock()
        membership_repo.find_role_id.return_value = uuid4()  # different from role.id
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = existing_user
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        uc = _make_usecase(
            membership_repo=membership_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
        )
        with pytest.raises(AlreadyMemberError):
            uc.execute(
                inviter_id=inviter.id,
                project_id=project.id,
                email="member@example.com",
                role_id=role.id,
            )
        # No state mutation should have occurred
        membership_repo.add.assert_not_called()


# ---------------------------------------------------------------------------
# Permission checks
# ---------------------------------------------------------------------------

class TestPermissionChecks:
    def test_inviter_without_perm_and_not_owner_raises(self):
        inviter = _make_user(has_invite_perm=False)
        project = _make_project(owner_id=uuid4())  # different owner
        role = _make_role()
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        uc = _make_usecase(user_repo=user_repo, project_repo=project_repo, role_repo=role_repo)

        with pytest.raises(PermissionDeniedError):
            uc.execute(
                inviter_id=inviter.id,
                project_id=project.id,
                email="someone@example.com",
                role_id=role.id,
            )

    def test_inviter_is_owner_without_perm_succeeds(self):
        """Project owner can invite even without explicit project:invite permission."""
        inviter = _make_user(has_invite_perm=False)
        project = _make_project(owner_id=inviter.id)  # same owner
        role = _make_role()
        inv_repo = MagicMock()
        inv_repo.find_pending_by_email_and_project.return_value = None
        inv_repo.count_created_today_by_project.return_value = 0
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = None
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        uc = _make_usecase(
            inv_repo=inv_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
        )
        result = uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="newuser@example.com",
            role_id=role.id,
        )
        assert result.kind == "invitation_sent"

    def test_superadmin_can_invite(self):
        inviter = _make_user(is_superadmin=True)
        project = _make_project(owner_id=uuid4())
        role = _make_role()
        inv_repo = MagicMock()
        inv_repo.find_pending_by_email_and_project.return_value = None
        inv_repo.count_created_today_by_project.return_value = 0
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = None
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        uc = _make_usecase(
            inv_repo=inv_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
        )
        result = uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="newuser@example.com",
            role_id=role.id,
        )
        assert result.kind == "invitation_sent"


# ---------------------------------------------------------------------------
# Guard: superadmin role not allowed
# ---------------------------------------------------------------------------

class TestRoleGuard:
    def test_superadmin_role_raises_role_not_allowed(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        superadmin_role = _make_role("superadmin")
        inv_repo = MagicMock()
        inv_repo.count_created_today_by_project.return_value = 0
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = None
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = superadmin_role

        uc = _make_usecase(
            inv_repo=inv_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
        )
        with pytest.raises(RoleNotAllowedError):
            uc.execute(
                inviter_id=inviter.id,
                project_id=project.id,
                email="newuser@example.com",
                role_id=superadmin_role.id,
            )


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------

class TestRateLimit:
    def test_per_project_50_daily_cap_raises(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        role = _make_role()
        inv_repo = MagicMock()
        inv_repo.count_created_today_by_project.return_value = 50  # at cap
        inv_repo.find_pending_by_email_and_project.return_value = None
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = None
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        uc = _make_usecase(
            inv_repo=inv_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
        )
        with pytest.raises(RateLimitedError):
            uc.execute(
                inviter_id=inviter.id,
                project_id=project.id,
                email="another@example.com",
                role_id=role.id,
            )


# ---------------------------------------------------------------------------
# Duplicate pending → revoke old, create new
# ---------------------------------------------------------------------------

class TestDuplicatePending:
    def test_revokes_old_pending_then_creates_new(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        role = _make_role()
        existing_inv, _ = Invitation.create(
            email="dup@example.com",
            project_id=project.id,
            role_id=role.id,
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
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        uc = _make_usecase(
            inv_repo=inv_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
        )
        uc.execute(
            inviter_id=inviter.id,
            project_id=project.id,
            email="dup@example.com",
            role_id=role.id,
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
                role_id=uuid4(),
            )

    def test_role_not_found_raises(self):
        inviter = _make_user(has_invite_perm=True)
        project = _make_project()
        inv_repo = MagicMock()
        inv_repo.count_created_today_by_project.return_value = 0
        inv_repo.find_pending_by_email_and_project.return_value = None
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = inviter
        user_repo.find_by_email.return_value = None
        project_repo = MagicMock()
        project_repo.find_by_id.return_value = project
        role_repo = MagicMock()
        role_repo.find_by_id.return_value = None

        uc = _make_usecase(
            inv_repo=inv_repo,
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
        )
        with pytest.raises(RoleNotFoundError):
            uc.execute(
                inviter_id=inviter.id,
                project_id=project.id,
                email="user@example.com",
                role_id=uuid4(),
            )
