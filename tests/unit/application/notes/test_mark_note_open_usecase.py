"""Unit tests for MarkNoteOpenUseCase — state transition back to 'open'."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.notes.exceptions import NoteNotFoundError, NotProjectMemberError
from app.application.notes.mark_note_open_usecase import MarkNoteOpenUseCase
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


def _make_note(*, project_id=None, status="done") -> Note:
    now = datetime(2026, 4, 27, 9, 0, 0, tzinfo=timezone.utc)
    return Note(
        id=uuid4(),
        project_id=project_id or uuid4(),
        created_by=uuid4(),
        title="Test note",
        description=None,
        due_date=date(2026, 5, 1),
        lead_time_minutes=0,
        status=status,
        created_at=now,
        updated_at=now,
    )


def _make_usecase(
    note_repo=None,
    membership_reader=None,
    db_session=None,
) -> MarkNoteOpenUseCase:
    return MarkNoteOpenUseCase(
        note_repo=note_repo or MagicMock(),
        membership_reader=membership_reader or MagicMock(),
        db_session=db_session or _FakeSession(),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestMarkNoteOpenHappyPath:
    def test_returns_dto_with_open_status(self):
        note = _make_note(status="done")
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        dto = uc.execute(actor_id=uuid4(), note_id=note.id)

        assert dto.status == "open"

    def test_note_repo_save_called_with_open_note(self):
        note = _make_note(status="done")
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        uc.execute(actor_id=uuid4(), note_id=note.id)

        note_repo.save.assert_called_once()
        saved = note_repo.save.call_args[0][0]
        assert saved.status == "open"

    def test_commit_called_once(self):
        note = _make_note(status="done")
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True
        db = _FakeSession()

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership, db_session=db)
        uc.execute(actor_id=uuid4(), note_id=note.id)

        assert db.commit_calls == 1

    def test_reopening_already_open_note_returns_open(self):
        """Idempotent: reopening an open note returns open DTO."""
        note = _make_note(status="open")
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        dto = uc.execute(actor_id=uuid4(), note_id=note.id)

        assert dto.status == "open"

    def test_dto_preserves_other_fields(self):
        project_id = uuid4()
        note = _make_note(project_id=project_id, status="done")
        note_repo = MagicMock()
        note_repo.find_by_id.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        dto = uc.execute(actor_id=uuid4(), note_id=note.id)

        assert dto.project_id == project_id
        assert dto.title == note.title
        assert dto.due_date == note.due_date


# ---------------------------------------------------------------------------
# Authorization / error paths
# ---------------------------------------------------------------------------


class TestMarkNoteOpenAuthz:
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
