"""Unit tests for UpdateNoteUseCase — journal model (title/description/category)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.notes.exceptions import (
    InvalidCategoryError,
    NoteNotFoundError,
    NotProjectMemberError,
)
from app.application.notes.update_note_usecase import UpdateNoteUseCase
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


def _make_note(
    *,
    project_id=None,
    title="Original title",
    description: str | None = None,
    category: str = "general",
    status: str = "open",
) -> Note:
    now = datetime(2026, 4, 27, 9, 0, 0, tzinfo=timezone.utc)
    return Note(
        id=uuid4(),
        project_id=project_id or uuid4(),
        created_by=uuid4(),
        title=title,
        description=description,
        category=category,
        status=status,
        created_at=now,
        updated_at=now,
    )


def _make_usecase(
    note_repo=None,
    membership_reader=None,
    db_session=None,
) -> UpdateNoteUseCase:
    return UpdateNoteUseCase(
        note_repo=note_repo or MagicMock(),
        membership_reader=membership_reader or MagicMock(),
        db_session=db_session or _FakeSession(),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestUpdateNoteHappyPath:
    def test_title_update_returns_updated_dto(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        note_repo.save = MagicMock()
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        dto = uc.execute(actor_id=uuid4(), note_id=note.id, title="New title")

        assert dto.title == "New title"

    def test_category_update_returns_updated_dto(self):
        note = _make_note(category="general")
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        dto = uc.execute(actor_id=uuid4(), note_id=note.id, category="inspection")

        assert dto.category == "inspection"

    def test_category_preserved_when_not_in_update(self):
        note = _make_note(category="delivery")
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        dto = uc.execute(actor_id=uuid4(), note_id=note.id, title="Only title changed")

        # category unchanged
        assert dto.category == "delivery"

    def test_commit_called_once(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True
        db = _FakeSession()

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership, db_session=db)
        uc.execute(actor_id=uuid4(), note_id=note.id, title="New")

        assert db.commit_calls == 1

    def test_note_repo_save_called(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        uc.execute(actor_id=uuid4(), note_id=note.id, title="Updated")

        note_repo.save.assert_called_once()


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestUpdateNoteAuthz:
    def test_non_member_raises_not_project_member_error(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = False

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        with pytest.raises(NotProjectMemberError):
            uc.execute(actor_id=uuid4(), note_id=note.id, title="Blocked")

    def test_note_not_found_raises_note_not_found_error(self):
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = None

        uc = _make_usecase(note_repo=note_repo)
        with pytest.raises(NoteNotFoundError):
            uc.execute(actor_id=uuid4(), note_id=uuid4(), title="Ghost note")

    def test_not_found_takes_priority_over_membership(self):
        """404 is checked before membership — find_by_id_for_update returns None."""
        membership = MagicMock()
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = None

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        with pytest.raises(NoteNotFoundError):
            uc.execute(actor_id=uuid4(), note_id=uuid4())

        membership.is_member.assert_not_called()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestUpdateNoteValidation:
    def test_invalid_category_raises(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        with pytest.raises(InvalidCategoryError):
            uc.execute(actor_id=uuid4(), note_id=note.id, category="invalid")

    def test_empty_title_raises_value_error(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        with pytest.raises(ValueError):
            uc.execute(actor_id=uuid4(), note_id=note.id, title="   ")

    def test_invalid_status_raises_value_error(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        with pytest.raises(ValueError):
            uc.execute(actor_id=uuid4(), note_id=note.id, status="pending")


# ---------------------------------------------------------------------------
# Status threading
# ---------------------------------------------------------------------------


class TestUpdateNoteStatus:
    """Status must thread through with_updates without dropping other fields."""

    def test_status_update_to_done(self):
        note = _make_note(status="open")
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        dto = uc.execute(actor_id=uuid4(), note_id=note.id, status="done")

        assert dto.status == "done"

    def test_status_unchanged_when_omitted(self):
        note = _make_note(status="done")
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        dto = uc.execute(actor_id=uuid4(), note_id=note.id, title="Only title changed")

        assert dto.status == "done"  # unchanged

    def test_status_only_preserves_other_fields(self):
        note = _make_note(title="Keep me", description="also keep", category="payment", status="open")
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        dto = uc.execute(actor_id=uuid4(), note_id=note.id, status="done")

        assert dto.status == "done"
        assert dto.title == "Keep me"
        assert dto.description == "also keep"
        assert dto.category == "payment"


# ---------------------------------------------------------------------------
# Description threading
# ---------------------------------------------------------------------------


class TestUpdateNoteDescription:
    """Description must be threaded correctly through with_updates."""

    def test_update_description_persists(self):
        note = _make_note(description=None)
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        dto = uc.execute(actor_id=uuid4(), note_id=note.id, description="new description")

        assert dto.description == "new description"

    def test_update_description_can_clear_to_none(self):
        note = _make_note(description="existing text")
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        dto = uc.execute(actor_id=uuid4(), note_id=note.id, description=None)

        assert dto.description is None

    def test_update_description_unchanged_when_keyword_omitted(self):
        """Calling execute() without description keyword leaves it unchanged."""
        note = _make_note(description="keep me")
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        dto = uc.execute(actor_id=uuid4(), note_id=note.id, title="only title changed")

        assert dto.description == "keep me"
