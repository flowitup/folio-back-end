"""Unit tests for ListProjectNotesUseCase — mocked repositories."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.notes.exceptions import NotProjectMemberError
from app.application.notes.list_project_notes_usecase import ListProjectNotesUseCase
from app.domain.entities.note import Note


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_note(*, project_id=None, category: str = "general") -> Note:
    now = datetime(2026, 4, 27, 9, 0, 0, tzinfo=timezone.utc)
    return Note(
        id=uuid4(),
        project_id=project_id or uuid4(),
        created_by=uuid4(),
        title="Test note",
        description=None,
        category=category,
        status="open",
        created_at=now,
        updated_at=now,
    )


def _make_usecase(note_repo=None, membership_reader=None) -> ListProjectNotesUseCase:
    return ListProjectNotesUseCase(
        note_repo=note_repo or MagicMock(),
        membership_reader=membership_reader or MagicMock(),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestListProjectNotesHappyPath:
    def test_returns_list_of_note_dtos(self):
        project_id = uuid4()
        notes = [_make_note(project_id=project_id), _make_note(project_id=project_id)]
        note_repo = MagicMock()
        note_repo.list_by_project.return_value = notes
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        result = uc.execute(actor_id=uuid4(), project_id=project_id)

        assert len(result) == 2

    def test_returns_empty_list_when_no_notes(self):
        project_id = uuid4()
        note_repo = MagicMock()
        note_repo.list_by_project.return_value = []
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        result = uc.execute(actor_id=uuid4(), project_id=project_id)

        assert result == []

    def test_queries_correct_project_id(self):
        project_id = uuid4()
        note_repo = MagicMock()
        note_repo.list_by_project.return_value = []
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        uc.execute(actor_id=uuid4(), project_id=project_id)

        note_repo.list_by_project.assert_called_once_with(project_id)

    def test_dto_fields_match_entity(self):
        project_id = uuid4()
        note = _make_note(project_id=project_id, category="inspection")
        note_repo = MagicMock()
        note_repo.list_by_project.return_value = [note]
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        result = uc.execute(actor_id=uuid4(), project_id=project_id)

        dto = result[0]
        assert dto.id == note.id
        assert dto.project_id == project_id
        assert dto.category == "inspection"

    def test_includes_multiple_categories_in_result(self):
        """list_by_project returns notes of all categories."""
        project_id = uuid4()
        note_a = _make_note(project_id=project_id, category="general")
        note_b = _make_note(project_id=project_id, category="delivery")
        note_repo = MagicMock()
        note_repo.list_by_project.return_value = [note_a, note_b]
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        result = uc.execute(actor_id=uuid4(), project_id=project_id)

        categories = {dto.category for dto in result}
        assert "general" in categories
        assert "delivery" in categories

    def test_no_db_session_needed(self):
        """ListProjectNotesUseCase is read-only — no session injected."""
        import inspect

        sig = inspect.signature(ListProjectNotesUseCase.__init__)
        assert "db_session" not in sig.parameters


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestListProjectNotesAuthz:
    def test_non_member_raises_not_project_member_error(self):
        membership = MagicMock()
        membership.is_member.return_value = False

        uc = _make_usecase(membership_reader=membership)
        with pytest.raises(NotProjectMemberError):
            uc.execute(actor_id=uuid4(), project_id=uuid4())

    def test_repo_not_called_when_not_member(self):
        membership = MagicMock()
        membership.is_member.return_value = False
        note_repo = MagicMock()

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        with pytest.raises(NotProjectMemberError):
            uc.execute(actor_id=uuid4(), project_id=uuid4())

        note_repo.list_by_project.assert_not_called()

    def test_membership_checked_with_correct_ids(self):
        actor_id = uuid4()
        project_id = uuid4()
        membership = MagicMock()
        membership.is_member.return_value = True
        note_repo = MagicMock()
        note_repo.list_by_project.return_value = []

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        uc.execute(actor_id=actor_id, project_id=project_id)

        membership.is_member.assert_called_once_with(actor_id, project_id)
