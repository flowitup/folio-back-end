"""Unit tests for DeleteNoteUseCase — mocked repositories."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.notes.delete_note_usecase import DeleteNoteUseCase
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
        title="Test note",
        description=None,
        due_date=date(2026, 5, 1),
        lead_time_minutes=0,
        status="open",
        created_at=now,
        updated_at=now,
    )


def _make_usecase(
    note_repo=None,
    membership_reader=None,
    db_session=None,
) -> DeleteNoteUseCase:
    return DeleteNoteUseCase(
        note_repo=note_repo or MagicMock(),
        membership_reader=membership_reader or MagicMock(),
        db_session=db_session or _FakeSession(),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestDeleteNoteHappyPath:
    def test_delete_calls_note_repo_delete(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        uc.execute(actor_id=uuid4(), note_id=note.id)

        note_repo.delete.assert_called_once_with(note.id)

    def test_delete_commits_once(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True
        db = _FakeSession()

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership, db_session=db)
        uc.execute(actor_id=uuid4(), note_id=note.id)

        assert db.commit_calls == 1

    def test_delete_returns_none(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        result = uc.execute(actor_id=uuid4(), note_id=note.id)

        assert result is None

    def test_membership_checked_against_note_project_id(self):
        project_id = uuid4()
        actor_id = uuid4()
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


class TestDeleteNoteAuthz:
    def test_note_not_found_raises(self):
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = None

        uc = _make_usecase(note_repo=note_repo)
        with pytest.raises(NoteNotFoundError):
            uc.execute(actor_id=uuid4(), note_id=uuid4())

    def test_non_member_raises(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = False

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        with pytest.raises(NotProjectMemberError):
            uc.execute(actor_id=uuid4(), note_id=note.id)

    def test_not_found_checked_before_membership(self):
        membership = MagicMock()
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = None

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        with pytest.raises(NoteNotFoundError):
            uc.execute(actor_id=uuid4(), note_id=uuid4())

        membership.is_member.assert_not_called()

    def test_repo_delete_not_called_on_non_member(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = False

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        with pytest.raises(NotProjectMemberError):
            uc.execute(actor_id=uuid4(), note_id=note.id)

        note_repo.delete.assert_not_called()
