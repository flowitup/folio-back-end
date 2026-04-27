"""Unit tests for BulkAddExistingUserUseCase — mocked repositories."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.admin.bulk_add_existing_user_usecase import BulkAddExistingUserUseCase
from app.application.admin.dtos import BulkAddStatus
from app.application.admin.exceptions import (
    EmptyProjectListError,
    PermissionDeniedError,
    RoleNotAllowedError,
    RoleNotFoundError,
    TargetUserNotFoundError,
    TooManyProjectsError,
)
from app.domain.entities.permission import Permission
from app.domain.entities.project import Project
from app.domain.entities.role import Role
from app.domain.entities.user import User

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_superadmin(id=None) -> User:
    user = User(
        id=id or uuid4(),
        email="superadmin@example.com",
        password_hash="hashed",
        is_active=True,
        created_at=datetime.now(timezone.utc),
        roles=[],
        display_name="Super Admin",
    )
    role = Role(id=uuid4(), name="superadmin")
    perm = Permission(id=uuid4(), name="*:*", resource="*", action="*")
    role.permissions.append(perm)
    user.roles.append(role)
    return user


def _make_user(id=None, email="target@example.com") -> User:
    return User(
        id=id or uuid4(),
        email=email,
        password_hash="hashed",
        is_active=True,
        created_at=datetime.now(timezone.utc),
        roles=[],
    )


def _make_project(name="Test Project") -> Project:
    return Project(
        id=uuid4(),
        name=name,
        owner_id=uuid4(),
        created_at=datetime.now(timezone.utc),
    )


def _make_role(name="member") -> Role:
    return Role(id=uuid4(), name=name)


class _FakeSession:
    """Test fake for the TransactionalSessionPort — counts commit() calls.

    H2 — the use-case explicitly commits before enqueueing the consolidated email
    so the queue write only fires for state that persisted. Tests assert the order:
    repo writes → commit → enqueue.
    """

    def __init__(self) -> None:
        self.commit_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1

    def begin_nested(self):
        # Unused by the bulk-add use-case, but the Protocol declares it.
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            yield self

        return _ctx()


def _make_usecase(
    user_repo=None,
    project_repo=None,
    role_repo=None,
    membership_repo=None,
    renderer=None,
    queue=None,
    db_session=None,
) -> BulkAddExistingUserUseCase:
    if renderer is None:
        renderer = MagicMock()
        renderer.render.return_value = ("Subject", "Text body", "<html>body</html>")
    return BulkAddExistingUserUseCase(
        user_repo=user_repo or MagicMock(),
        project_repo=project_repo or MagicMock(),
        role_repo=role_repo or MagicMock(),
        membership_repo=membership_repo or MagicMock(),
        email_renderer=renderer,
        queue_port=queue or MagicMock(),
        app_base_url="http://localhost:3000",
        db_session=db_session or _FakeSession(),
    )


# ---------------------------------------------------------------------------
# Happy path: all projects added
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_all_added_returns_added_status(self):
        requester = _make_superadmin()
        target = _make_user()
        role = _make_role()
        p1 = _make_project("Project One")
        p2 = _make_project("Project Two")

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        project_repo = MagicMock()
        project_repo.find_by_id.side_effect = lambda pid: p1 if pid == p1.id else p2

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        membership_repo = MagicMock()
        membership_repo.find_role_id.return_value = None  # not yet a member
        membership_repo.add.return_value = True

        uc = _make_usecase(
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            membership_repo=membership_repo,
        )
        result = uc.execute(
            requester_id=requester.id,
            target_user_id=target.id,
            project_ids=[p1.id, p2.id],
            role_id=role.id,
        )

        assert len(result.results) == 2
        assert all(r.status == BulkAddStatus.ADDED for r in result.results)

    def test_membership_repo_add_called_once_per_project(self):
        requester = _make_superadmin()
        target = _make_user()
        role = _make_role()
        p1 = _make_project("P1")
        p2 = _make_project("P2")

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        project_repo = MagicMock()
        project_repo.find_by_id.side_effect = lambda pid: p1 if pid == p1.id else p2

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        membership_repo = MagicMock()
        membership_repo.find_role_id.return_value = None
        membership_repo.add.return_value = True

        uc = _make_usecase(
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            membership_repo=membership_repo,
        )
        uc.execute(
            requester_id=requester.id,
            target_user_id=target.id,
            project_ids=[p1.id, p2.id],
            role_id=role.id,
        )

        assert membership_repo.add.call_count == 2

    def test_queue_enqueue_called_once_for_consolidated_email(self):
        requester = _make_superadmin()
        target = _make_user()
        role = _make_role()
        p1 = _make_project("P1")
        p2 = _make_project("P2")

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        project_repo = MagicMock()
        project_repo.find_by_id.side_effect = lambda pid: p1 if pid == p1.id else p2

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        membership_repo = MagicMock()
        membership_repo.find_role_id.return_value = None
        membership_repo.add.return_value = True

        queue = MagicMock()
        uc = _make_usecase(
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            membership_repo=membership_repo,
            queue=queue,
        )
        uc.execute(
            requester_id=requester.id,
            target_user_id=target.id,
            project_ids=[p1.id, p2.id],
            role_id=role.id,
        )

        queue.enqueue.assert_called_once()
        assert queue.enqueue.call_args[0][0] == "tasks.send_email"


# ---------------------------------------------------------------------------
# Already-member branches
# ---------------------------------------------------------------------------


class TestAlreadyMember:
    def test_same_role_returns_already_member_same_role(self):
        requester = _make_superadmin()
        target = _make_user()
        role = _make_role()
        p1 = _make_project()

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        project_repo = MagicMock()
        project_repo.find_by_id.return_value = p1

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        membership_repo = MagicMock()
        membership_repo.find_role_id.return_value = role.id  # same role
        membership_repo.add.return_value = False

        uc = _make_usecase(
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            membership_repo=membership_repo,
        )
        result = uc.execute(
            requester_id=requester.id,
            target_user_id=target.id,
            project_ids=[p1.id],
            role_id=role.id,
        )

        assert result.results[0].status == BulkAddStatus.ALREADY_MEMBER_SAME_ROLE
        # H1 — under the new contract `add()` IS called every iteration; the bool return
        # (False, here) signals "row already existed, no INSERT performed".
        membership_repo.add.assert_called_once()

    def test_same_role_not_in_email_recap(self):
        requester = _make_superadmin()
        target = _make_user()
        role = _make_role()
        p1 = _make_project()

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        project_repo = MagicMock()
        project_repo.find_by_id.return_value = p1

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        membership_repo = MagicMock()
        membership_repo.find_role_id.return_value = role.id
        membership_repo.add.return_value = False

        queue = MagicMock()
        uc = _make_usecase(
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            membership_repo=membership_repo,
            queue=queue,
        )
        uc.execute(
            requester_id=requester.id,
            target_user_id=target.id,
            project_ids=[p1.id],
            role_id=role.id,
        )

        # No added projects → no email
        queue.enqueue.assert_not_called()

    def test_different_role_returns_already_member_different_role(self):
        requester = _make_superadmin()
        target = _make_user()
        role = _make_role()
        p1 = _make_project()

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        project_repo = MagicMock()
        project_repo.find_by_id.return_value = p1

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        membership_repo = MagicMock()
        membership_repo.find_role_id.return_value = uuid4()  # different role
        membership_repo.add.return_value = False

        uc = _make_usecase(
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            membership_repo=membership_repo,
        )
        result = uc.execute(
            requester_id=requester.id,
            target_user_id=target.id,
            project_ids=[p1.id],
            role_id=role.id,
        )

        assert result.results[0].status == BulkAddStatus.ALREADY_MEMBER_DIFFERENT_ROLE
        # H1 — `add()` IS called; `False` return tells us the conflict happened and
        # we left the existing row untouched (no role override).
        membership_repo.add.assert_called_once()

    def test_different_role_not_in_email_recap(self):
        requester = _make_superadmin()
        target = _make_user()
        role = _make_role()
        p1 = _make_project()

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        project_repo = MagicMock()
        project_repo.find_by_id.return_value = p1

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        membership_repo = MagicMock()
        membership_repo.find_role_id.return_value = uuid4()
        membership_repo.add.return_value = False

        queue = MagicMock()
        uc = _make_usecase(
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            membership_repo=membership_repo,
            queue=queue,
        )
        uc.execute(
            requester_id=requester.id,
            target_user_id=target.id,
            project_ids=[p1.id],
            role_id=role.id,
        )

        queue.enqueue.assert_not_called()

    def test_h1_race_regression_add_returning_false_does_not_yield_added(self):
        """H1 — if the repo's `add()` returns False (ON CONFLICT — row already existed),
        the use-case must NOT report ADDED, must NOT include the project in the email recap.

        Simulates the concurrent-bulk-add race: the use-case looked up `find_role_id` and saw
        None (a moment before another caller inserted), then `add()` runs but returns False.
        """
        requester = _make_superadmin()
        target = _make_user()
        role = _make_role()
        p1 = _make_project()

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        project_repo = MagicMock()
        project_repo.find_by_id.return_value = p1

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        membership_repo = MagicMock()
        # Pre-add view says "no membership" (race-window observation)
        # Post-add `find_role_id` (the use-case calls it on conflict) reports the now-existing role.
        membership_repo.find_role_id.return_value = role.id
        # add() reports the conflict — row was inserted by someone else mid-flight.
        membership_repo.add.return_value = False

        queue = MagicMock()
        uc = _make_usecase(
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            membership_repo=membership_repo,
            queue=queue,
        )
        result = uc.execute(
            requester_id=requester.id,
            target_user_id=target.id,
            project_ids=[p1.id],
            role_id=role.id,
        )

        # Must report ALREADY_MEMBER_*, not ADDED
        assert result.results[0].status == BulkAddStatus.ALREADY_MEMBER_SAME_ROLE
        # Must NOT enqueue the consolidated email (no actual additions)
        queue.enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# Project not found branch
# ---------------------------------------------------------------------------


class TestProjectNotFound:
    def test_missing_project_returns_project_not_found_status(self):
        requester = _make_superadmin()
        target = _make_user()
        role = _make_role()
        missing_pid = uuid4()

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        project_repo = MagicMock()
        project_repo.find_by_id.return_value = None

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        uc = _make_usecase(
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
        )
        result = uc.execute(
            requester_id=requester.id,
            target_user_id=target.id,
            project_ids=[missing_pid],
            role_id=role.id,
        )

        assert result.results[0].status == BulkAddStatus.PROJECT_NOT_FOUND
        assert result.results[0].project_id == missing_pid
        assert result.results[0].project_name is None

    def test_loop_continues_after_missing_project(self):
        requester = _make_superadmin()
        target = _make_user()
        role = _make_role()
        missing_pid = uuid4()
        good_project = _make_project("Good Project")

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        project_repo = MagicMock()
        project_repo.find_by_id.side_effect = lambda pid: None if pid == missing_pid else good_project

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        membership_repo = MagicMock()
        membership_repo.find_role_id.return_value = None
        membership_repo.add.return_value = True

        uc = _make_usecase(
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            membership_repo=membership_repo,
        )
        result = uc.execute(
            requester_id=requester.id,
            target_user_id=target.id,
            project_ids=[missing_pid, good_project.id],
            role_id=role.id,
        )

        assert len(result.results) == 2
        statuses = {r.project_id: r.status for r in result.results}
        assert statuses[missing_pid] == BulkAddStatus.PROJECT_NOT_FOUND
        assert statuses[good_project.id] == BulkAddStatus.ADDED


# ---------------------------------------------------------------------------
# Guard: missing entities
# ---------------------------------------------------------------------------


class TestMissingEntities:
    def test_target_user_not_found_raises(self):
        requester = _make_superadmin()
        role = _make_role()

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else None

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        uc = _make_usecase(user_repo=user_repo, role_repo=role_repo)
        with pytest.raises(TargetUserNotFoundError):
            uc.execute(
                requester_id=requester.id,
                target_user_id=uuid4(),
                project_ids=[uuid4()],
                role_id=role.id,
            )

    def test_role_not_found_raises(self):
        requester = _make_superadmin()
        target = _make_user()

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = None

        uc = _make_usecase(user_repo=user_repo, role_repo=role_repo)
        with pytest.raises(RoleNotFoundError):
            uc.execute(
                requester_id=requester.id,
                target_user_id=target.id,
                project_ids=[uuid4()],
                role_id=uuid4(),
            )

    def test_superadmin_role_raises_role_not_allowed(self):
        requester = _make_superadmin()
        target = _make_user()
        superadmin_role = _make_role("superadmin")

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = superadmin_role

        uc = _make_usecase(user_repo=user_repo, role_repo=role_repo)
        with pytest.raises(RoleNotAllowedError):
            uc.execute(
                requester_id=requester.id,
                target_user_id=target.id,
                project_ids=[uuid4()],
                role_id=superadmin_role.id,
            )


# ---------------------------------------------------------------------------
# Permission guard
# ---------------------------------------------------------------------------


class TestPermissionGuard:
    def test_requester_not_found_raises_permission_denied(self):
        user_repo = MagicMock()
        user_repo.find_by_id.return_value = None

        uc = _make_usecase(user_repo=user_repo)
        with pytest.raises(PermissionDeniedError):
            uc.execute(
                requester_id=uuid4(),
                target_user_id=uuid4(),
                project_ids=[uuid4()],
                role_id=uuid4(),
            )

    def test_requester_without_star_perm_raises_permission_denied(self):
        # User with only project:read, not *:*
        requester = _make_user(email="limited@example.com")
        role = Role(id=uuid4(), name="member")
        perm = Permission(id=uuid4(), name="project:read", resource="project", action="read")
        role.permissions.append(perm)
        requester.roles.append(role)

        user_repo = MagicMock()
        user_repo.find_by_id.return_value = requester

        uc = _make_usecase(user_repo=user_repo)
        with pytest.raises(PermissionDeniedError):
            uc.execute(
                requester_id=requester.id,
                target_user_id=uuid4(),
                project_ids=[uuid4()],
                role_id=uuid4(),
            )


# ---------------------------------------------------------------------------
# Input validation guards
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_empty_project_ids_raises(self):
        requester = _make_superadmin()
        target = _make_user()
        role = _make_role()

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        uc = _make_usecase(user_repo=user_repo, role_repo=role_repo)
        with pytest.raises(EmptyProjectListError):
            uc.execute(
                requester_id=requester.id,
                target_user_id=target.id,
                project_ids=[],
                role_id=role.id,
            )

    def test_too_many_project_ids_raises(self):
        requester = _make_superadmin()
        target = _make_user()
        role = _make_role()

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        uc = _make_usecase(user_repo=user_repo, role_repo=role_repo)
        with pytest.raises(TooManyProjectsError):
            uc.execute(
                requester_id=requester.id,
                target_user_id=target.id,
                project_ids=[uuid4() for _ in range(51)],
                role_id=role.id,
            )

    def test_duplicate_project_ids_are_deduped(self):
        requester = _make_superadmin()
        target = _make_user()
        role = _make_role()
        p1 = _make_project()
        dup_id = p1.id

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        project_repo = MagicMock()
        project_repo.find_by_id.return_value = p1

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        membership_repo = MagicMock()
        membership_repo.find_role_id.return_value = None
        membership_repo.add.return_value = True

        uc = _make_usecase(
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            membership_repo=membership_repo,
        )
        # Send the same project id three times
        result = uc.execute(
            requester_id=requester.id,
            target_user_id=target.id,
            project_ids=[dup_id, dup_id, dup_id],
            role_id=role.id,
        )

        # Deduped to 1 unique project
        assert len(result.results) == 1
        membership_repo.add.assert_called_once()


# ---------------------------------------------------------------------------
# Mixed batch: consolidated email only includes added projects
# ---------------------------------------------------------------------------


class TestMixedBatch:
    def test_mixed_batch_results_populated_correctly(self):
        requester = _make_superadmin()
        target = _make_user()
        role = _make_role()

        p_add = _make_project("Will Be Added")
        p_same = _make_project("Same Role Already")
        p_diff = _make_project("Different Role Already")
        missing_pid = uuid4()

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        same_role_id = role.id
        diff_role_id = uuid4()

        def project_side_effect(pid):
            if pid == p_add.id:
                return p_add
            if pid == p_same.id:
                return p_same
            if pid == p_diff.id:
                return p_diff
            return None  # missing_pid

        def membership_side_effect(uid, pid):
            if pid == p_same.id:
                return same_role_id
            if pid == p_diff.id:
                return diff_role_id
            return None  # not a member

        # H1 — repo.add returns True only on actual INSERT (no conflict).
        # In a real DB, a row at p_same/p_diff already exists; the INSERT...ON CONFLICT
        # DO NOTHING reports False. p_add has no row → True.
        def add_side_effect(membership):
            if membership.project_id == p_add.id:
                return True
            return False

        project_repo = MagicMock()
        project_repo.find_by_id.side_effect = project_side_effect

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        membership_repo = MagicMock()
        membership_repo.find_role_id.side_effect = membership_side_effect
        membership_repo.add.side_effect = add_side_effect

        queue = MagicMock()
        renderer = MagicMock()
        renderer.render.return_value = ("Subj", "Body", "<html/>")

        uc = _make_usecase(
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            membership_repo=membership_repo,
            queue=queue,
            renderer=renderer,
        )
        result = uc.execute(
            requester_id=requester.id,
            target_user_id=target.id,
            project_ids=[p_add.id, p_same.id, p_diff.id, missing_pid],
            role_id=role.id,
        )

        statuses = {r.project_id: r.status for r in result.results}
        assert statuses[p_add.id] == BulkAddStatus.ADDED
        assert statuses[p_same.id] == BulkAddStatus.ALREADY_MEMBER_SAME_ROLE
        assert statuses[p_diff.id] == BulkAddStatus.ALREADY_MEMBER_DIFFERENT_ROLE
        assert statuses[missing_pid] == BulkAddStatus.PROJECT_NOT_FOUND

    def test_consolidated_email_only_contains_added_projects(self):
        requester = _make_superadmin()
        target = _make_user()
        role = _make_role()

        p_add = _make_project("Added Project")
        p_same = _make_project("Same Role")
        missing_pid = uuid4()

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        def project_side_effect(pid):
            if pid == p_add.id:
                return p_add
            if pid == p_same.id:
                return p_same
            return None

        def membership_side_effect(uid, pid):
            if pid == p_same.id:
                return role.id
            return None

        def add_side_effect(membership):
            return membership.project_id == p_add.id  # only p_add gets a real INSERT

        project_repo = MagicMock()
        project_repo.find_by_id.side_effect = project_side_effect

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        membership_repo = MagicMock()
        membership_repo.find_role_id.side_effect = membership_side_effect
        membership_repo.add.side_effect = add_side_effect

        queue = MagicMock()
        renderer = MagicMock()
        renderer.render.return_value = ("Subj", "Body", "<html/>")

        uc = _make_usecase(
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            membership_repo=membership_repo,
            queue=queue,
            renderer=renderer,
        )
        uc.execute(
            requester_id=requester.id,
            target_user_id=target.id,
            project_ids=[p_add.id, p_same.id, missing_pid],
            role_id=role.id,
        )

        # Email enqueued exactly once (only for p_add)
        queue.enqueue.assert_called_once()

        # Renderer called with only the added project in the context
        renderer.render.assert_called_once()
        ctx = renderer.render.call_args[0][2]  # positional arg index 2 = ctx dict
        assert len(ctx["added_projects"]) == 1
        assert ctx["added_projects"][0]["name"] == p_add.name

    def test_no_added_projects_no_email_enqueued(self):
        requester = _make_superadmin()
        target = _make_user()
        role = _make_role()
        p_same = _make_project()

        user_repo = MagicMock()
        user_repo.find_by_id.side_effect = lambda uid: requester if uid == requester.id else target

        project_repo = MagicMock()
        project_repo.find_by_id.return_value = p_same

        role_repo = MagicMock()
        role_repo.find_by_id.return_value = role

        membership_repo = MagicMock()
        membership_repo.find_role_id.return_value = role.id  # already member same role
        membership_repo.add.return_value = False

        queue = MagicMock()
        uc = _make_usecase(
            user_repo=user_repo,
            project_repo=project_repo,
            role_repo=role_repo,
            membership_repo=membership_repo,
            queue=queue,
        )
        uc.execute(
            requester_id=requester.id,
            target_user_id=target.id,
            project_ids=[p_same.id],
            role_id=role.id,
        )

        queue.enqueue.assert_not_called()
