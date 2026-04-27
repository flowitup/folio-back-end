"""Unit tests for UpdateNoteUseCase — including dismissal-cascade tests."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.notes.exceptions import (
    InvalidLeadTimeError,
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
    due_date=date(2026, 5, 1),
    lead_time_minutes=0,
    status="open",
) -> Note:
    now = datetime(2026, 4, 27, 9, 0, 0, tzinfo=timezone.utc)
    return Note(
        id=uuid4(),
        project_id=project_id or uuid4(),
        created_by=uuid4(),
        title=title,
        description=description,
        due_date=due_date,
        lead_time_minutes=lead_time_minutes,
        status=status,
        created_at=now,
        updated_at=now,
    )


def _make_usecase(
    note_repo=None,
    dismissal_repo=None,
    membership_reader=None,
    db_session=None,
) -> UpdateNoteUseCase:
    return UpdateNoteUseCase(
        note_repo=note_repo or MagicMock(),
        dismissal_repo=dismissal_repo or MagicMock(),
        membership_reader=membership_reader or MagicMock(),
        db_session=db_session or _FakeSession(),
    )


# ---------------------------------------------------------------------------
# Happy path — title only, dismissals UNCHANGED
# ---------------------------------------------------------------------------


class TestUpdateNoteHappyPath:
    def test_title_update_returns_updated_dto(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        note_repo.save = MagicMock()
        membership = MagicMock()
        membership.is_member.return_value = True
        dismissal_repo = MagicMock()

        uc = _make_usecase(
            note_repo=note_repo,
            dismissal_repo=dismissal_repo,
            membership_reader=membership,
        )
        dto = uc.execute(
            actor_id=uuid4(),
            note_id=note.id,
            title="New title",
        )

        assert dto.title == "New title"

    def test_edit_only_title_dismissals_unchanged(self):
        """Critical: editing title only must NOT cascade-delete dismissals."""
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True
        dismissal_repo = MagicMock()

        uc = _make_usecase(
            note_repo=note_repo,
            dismissal_repo=dismissal_repo,
            membership_reader=membership,
        )
        uc.execute(
            actor_id=uuid4(),
            note_id=note.id,
            title="Only title changed",
        )

        dismissal_repo.delete_all_for_note.assert_not_called()

    def test_edit_same_due_date_no_cascade(self):
        """Setting due_date to its current value must not trigger cascade."""
        note = _make_note(due_date=date(2026, 5, 1))
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True
        dismissal_repo = MagicMock()

        uc = _make_usecase(
            note_repo=note_repo,
            dismissal_repo=dismissal_repo,
            membership_reader=membership,
        )
        uc.execute(
            actor_id=uuid4(),
            note_id=note.id,
            due_date=date(2026, 5, 1),  # same value
        )

        dismissal_repo.delete_all_for_note.assert_not_called()

    def test_edit_same_lead_time_no_cascade(self):
        note = _make_note(lead_time_minutes=60)
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True
        dismissal_repo = MagicMock()

        uc = _make_usecase(
            note_repo=note_repo,
            dismissal_repo=dismissal_repo,
            membership_reader=membership,
        )
        uc.execute(
            actor_id=uuid4(),
            note_id=note.id,
            lead_time_minutes=60,  # same value
        )

        dismissal_repo.delete_all_for_note.assert_not_called()

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
# Dismissal-cascade tests (CRITICAL)
# ---------------------------------------------------------------------------


class TestDismissalCascade:
    def test_edit_due_date_cascades_dismissals(self):
        """CRITICAL: changing due_date must delete all dismissals in same TX."""
        note = _make_note(due_date=date(2026, 5, 1))
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True
        dismissal_repo = MagicMock()

        uc = _make_usecase(
            note_repo=note_repo,
            dismissal_repo=dismissal_repo,
            membership_reader=membership,
        )
        uc.execute(
            actor_id=uuid4(),
            note_id=note.id,
            due_date=date(2026, 6, 1),  # changed
        )

        dismissal_repo.delete_all_for_note.assert_called_once_with(note.id)

    def test_edit_lead_time_cascades_dismissals(self):
        """CRITICAL: changing lead_time_minutes must delete all dismissals."""
        note = _make_note(lead_time_minutes=0)
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True
        dismissal_repo = MagicMock()

        uc = _make_usecase(
            note_repo=note_repo,
            dismissal_repo=dismissal_repo,
            membership_reader=membership,
        )
        uc.execute(
            actor_id=uuid4(),
            note_id=note.id,
            lead_time_minutes=60,  # changed
        )

        dismissal_repo.delete_all_for_note.assert_called_once_with(note.id)

    def test_edit_both_schedule_fields_cascades_once(self):
        """Changing both due_date and lead_time should cascade (only once)."""
        note = _make_note(due_date=date(2026, 5, 1), lead_time_minutes=0)
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True
        dismissal_repo = MagicMock()

        uc = _make_usecase(
            note_repo=note_repo,
            dismissal_repo=dismissal_repo,
            membership_reader=membership,
        )
        uc.execute(
            actor_id=uuid4(),
            note_id=note.id,
            due_date=date(2026, 6, 1),
            lead_time_minutes=60,
        )

        # delete_all_for_note is called exactly once (schedule_changed is a bool)
        dismissal_repo.delete_all_for_note.assert_called_once_with(note.id)

    def test_cascade_fires_before_save(self):
        """Order: dismissal delete must happen before note save (within TX)."""
        call_order: list[str] = []
        note = _make_note(due_date=date(2026, 5, 1))

        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        note_repo.save.side_effect = lambda _n: call_order.append("save")

        membership = MagicMock()
        membership.is_member.return_value = True

        dismissal_repo = MagicMock()
        dismissal_repo.delete_all_for_note.side_effect = lambda _id: call_order.append("delete")

        uc = _make_usecase(
            note_repo=note_repo,
            dismissal_repo=dismissal_repo,
            membership_reader=membership,
        )
        uc.execute(
            actor_id=uuid4(),
            note_id=note.id,
            due_date=date(2026, 6, 1),
        )

        assert call_order == ["delete", "save"]


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
    def test_invalid_lead_time_raises(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        with pytest.raises(InvalidLeadTimeError):
            uc.execute(
                actor_id=uuid4(),
                note_id=note.id,
                lead_time_minutes=30,  # invalid
            )

    def test_empty_title_raises_value_error(self):
        note = _make_note()
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        with pytest.raises(ValueError):
            uc.execute(
                actor_id=uuid4(),
                note_id=note.id,
                title="   ",
            )


# ---------------------------------------------------------------------------
# C1 regression — description threading
# ---------------------------------------------------------------------------


class TestUpdateNoteDescription:
    """Regression tests for C1: description must be threaded through with_updates."""

    def test_update_description_persists(self):
        """PATCH with description='new value' must update the stored description."""
        note = _make_note(description=None)
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        dto = uc.execute(
            actor_id=uuid4(),
            note_id=note.id,
            description="new description",
        )

        assert dto.description == "new description"

    def test_update_description_can_clear_to_none(self):
        """PATCH with description=None must clear an existing description."""
        note = _make_note(description="existing text")
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        dto = uc.execute(
            actor_id=uuid4(),
            note_id=note.id,
            description=None,
        )

        assert dto.description is None

    def test_update_description_unchanged_when_keyword_omitted(self):
        """Calling execute() without description keyword leaves it unchanged."""
        note = _make_note(description="keep me")
        note_repo = MagicMock()
        note_repo.find_by_id_for_update.return_value = note
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        # No description= kwarg → uses default _UNSET sentinel
        dto = uc.execute(
            actor_id=uuid4(),
            note_id=note.id,
            title="only title changed",
        )

        assert dto.description == "keep me"
