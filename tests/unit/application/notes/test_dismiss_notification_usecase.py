"""Unit tests for DismissNotificationUseCase — happy path + 403 + idempotency."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.notes.dismiss_notification_usecase import DismissNotificationUseCase
from app.application.notes.exceptions import NoteNotFoundError, NotProjectMemberError
from app.domain.entities.note import Note


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSession:
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


def _make_note(*, project_id=None) -> Note:
    now = datetime(2026, 4, 27, 9, 0, 0, tzinfo=timezone.utc)
    return Note(
        id=uuid4(),
        project_id=project_id or uuid4(),
        created_by=uuid4(),
        title="Dismissible note",
        description=None,
        due_date=date(2026, 5, 1),
        lead_time_minutes=0,
        status="open",
        created_at=now,
        updated_at=now,
    )


def _make_usecase(
    note_repo=None,
    dismissal_repo=None,
    membership_reader=None,
    db_session=None,
) -> DismissNotificationUseCase:
    return DismissNotificationUseCase(
        note_repo=note_repo or MagicMock(),
        dismissal_repo=dismissal_repo or MagicMock(),
        membership_reader=membership_reader or MagicMock(),
        db_session=db_session or _FakeSession(),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestDismissNotificationHappyPath:
    def test_dismissal_repo_add_called_with_correct_ids(self):
        actor_id = uuid4()
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True
        dismissal_repo = MagicMock()
        db = _FakeSession()

        uc = _make_usecase(
            note_repo=note_repo,
            dismissal_repo=dismissal_repo,
            membership_reader=membership,
            db_session=db,
        )
        uc.execute(actor_id=actor_id, note_id=note.id)

        dismissal_repo.add.assert_called_once_with(actor_id, note.id)

    def test_commit_called_once(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True
        db = _FakeSession()

        uc = _make_usecase(
            note_repo=note_repo,
            membership_reader=membership,
            db_session=db,
        )
        uc.execute(actor_id=uuid4(), note_id=note.id)

        assert db.commit_calls == 1

    def test_returns_none(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        result = uc.execute(actor_id=uuid4(), note_id=note.id)

        assert result is None

    def test_idempotent_dismiss_twice_is_no_op(self):
        """Second dismiss call is safe — dismissal_repo.add is idempotent at infra level.
        Use-case calls add() each time; infra handles ON CONFLICT DO NOTHING."""
        actor_id = uuid4()
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True
        dismissal_repo = MagicMock()
        db = _FakeSession()

        uc = _make_usecase(
            note_repo=note_repo,
            dismissal_repo=dismissal_repo,
            membership_reader=membership,
            db_session=db,
        )
        # First dismiss
        uc.execute(actor_id=actor_id, note_id=note.id)
        # Second dismiss — must not raise
        uc.execute(actor_id=actor_id, note_id=note.id)

        assert dismissal_repo.add.call_count == 2

    def test_concurrent_dismiss_by_two_members_both_succeed(self):
        """Two different members dismissing the same note both succeed (composite PK)."""
        member_a = uuid4()
        member_b = uuid4()
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True
        dismissal_repo = MagicMock()

        uc = _make_usecase(
            note_repo=note_repo,
            dismissal_repo=dismissal_repo,
            membership_reader=membership,
        )
        uc.execute(actor_id=member_a, note_id=note.id)
        uc.execute(actor_id=member_b, note_id=note.id)

        # Both dismissals recorded with distinct actor IDs
        calls = dismissal_repo.add.call_args_list
        assert len(calls) == 2
        called_user_ids = {c[0][0] for c in calls}
        assert member_a in called_user_ids
        assert member_b in called_user_ids

    def test_membership_checked_against_note_project(self):
        actor_id = uuid4()
        project_id = uuid4()
        note = _make_note(project_id=project_id)
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        uc.execute(actor_id=actor_id, note_id=note.id)

        membership.is_member.assert_called_once_with(actor_id, project_id)


# ---------------------------------------------------------------------------
# Authorization / error paths
# ---------------------------------------------------------------------------


class TestDismissNotificationAuthz:
    def test_note_not_found_raises(self):
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = None

        uc = _make_usecase(note_repo=note_repo)
        with pytest.raises(NoteNotFoundError):
            uc.execute(actor_id=uuid4(), note_id=uuid4())

    def test_non_member_raises_not_project_member_error(self):
        """User not in the note's project gets 403."""
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = False

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        with pytest.raises(NotProjectMemberError):
            uc.execute(actor_id=uuid4(), note_id=note.id)

    def test_not_found_before_membership_check(self):
        membership = MagicMock()
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = None

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        with pytest.raises(NoteNotFoundError):
            uc.execute(actor_id=uuid4(), note_id=uuid4())

        membership.is_member.assert_not_called()

    def test_dismissal_repo_not_called_for_non_member(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = False
        dismissal_repo = MagicMock()

        uc = _make_usecase(
            note_repo=note_repo,
            dismissal_repo=dismissal_repo,
            membership_reader=membership,
        )
        with pytest.raises(NotProjectMemberError):
            uc.execute(actor_id=uuid4(), note_id=note.id)

        dismissal_repo.add.assert_not_called()
