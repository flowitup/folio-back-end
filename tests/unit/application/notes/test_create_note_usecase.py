"""Unit tests for CreateNoteUseCase — mocked repositories."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.notes.create_note_usecase import CreateNoteUseCase
from app.application.notes.exceptions import InvalidCategoryError, NotProjectMemberError


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal TransactionalSessionPort fake that counts commit() calls."""

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
    note_repo=None,
    membership_reader=None,
    db_session=None,
) -> CreateNoteUseCase:
    return CreateNoteUseCase(
        note_repo=note_repo or MagicMock(),
        membership_reader=membership_reader or MagicMock(),
        db_session=db_session or _FakeSession(),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestCreateNoteHappyPath:
    def test_returns_note_dto_with_correct_fields(self):
        actor_id = uuid4()
        project_id = uuid4()
        membership = MagicMock()
        membership.is_member.return_value = True
        note_repo = MagicMock()
        db = _FakeSession()

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership, db_session=db)
        dto = uc.execute(
            actor_id=actor_id,
            project_id=project_id,
            title="Fix the roof",
            description="urgent",
            category="inspection",
        )

        assert dto.title == "Fix the roof"
        assert dto.description == "urgent"
        assert dto.category == "inspection"
        assert dto.project_id == project_id
        assert dto.created_by == actor_id

    def test_default_category_is_general(self):
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(membership_reader=membership)
        dto = uc.execute(
            actor_id=uuid4(),
            project_id=uuid4(),
            title="Default cat",
            description=None,
        )

        assert dto.category == "general"

    def test_note_repo_add_called_once(self):
        membership = MagicMock()
        membership.is_member.return_value = True
        note_repo = MagicMock()

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        uc.execute(
            actor_id=uuid4(),
            project_id=uuid4(),
            title="Test note",
            description=None,
        )

        note_repo.add.assert_called_once()

    def test_commit_called_once(self):
        membership = MagicMock()
        membership.is_member.return_value = True
        db = _FakeSession()

        uc = _make_usecase(membership_reader=membership, db_session=db)
        uc.execute(
            actor_id=uuid4(),
            project_id=uuid4(),
            title="Commit test",
            description=None,
        )

        assert db.commit_calls == 1

    def test_description_none_allowed(self):
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(membership_reader=membership)
        dto = uc.execute(
            actor_id=uuid4(),
            project_id=uuid4(),
            title="No description",
            description=None,
        )

        assert dto.description is None

    def test_title_200_chars_boundary(self):
        """Title at exactly 200 characters must be accepted."""
        membership = MagicMock()
        membership.is_member.return_value = True
        title = "A" * 200

        uc = _make_usecase(membership_reader=membership)
        dto = uc.execute(
            actor_id=uuid4(),
            project_id=uuid4(),
            title=title,
            description=None,
        )

        assert len(dto.title) == 200

    def test_all_valid_categories_accepted(self):
        """All 6 journal categories must be accepted."""
        membership = MagicMock()
        membership.is_member.return_value = True

        for cat in ("inspection", "delivery", "payment", "decision", "call", "general"):
            uc = _make_usecase(membership_reader=membership)
            dto = uc.execute(
                actor_id=uuid4(),
                project_id=uuid4(),
                title=f"Note {cat}",
                description=None,
                category=cat,
            )
            assert dto.category == cat


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestCreateNoteAuthz:
    def test_non_member_raises_not_project_member_error(self):
        membership = MagicMock()
        membership.is_member.return_value = False

        uc = _make_usecase(membership_reader=membership)
        with pytest.raises(NotProjectMemberError):
            uc.execute(
                actor_id=uuid4(),
                project_id=uuid4(),
                title="Nope",
                description=None,
            )

    def test_membership_check_uses_correct_ids(self):
        actor_id = uuid4()
        project_id = uuid4()
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(membership_reader=membership)
        uc.execute(
            actor_id=actor_id,
            project_id=project_id,
            title="Check IDs",
            description=None,
        )

        membership.is_member.assert_called_once_with(actor_id, project_id)

    def test_note_repo_not_called_when_not_member(self):
        membership = MagicMock()
        membership.is_member.return_value = False
        note_repo = MagicMock()

        uc = _make_usecase(note_repo=note_repo, membership_reader=membership)
        with pytest.raises(NotProjectMemberError):
            uc.execute(
                actor_id=uuid4(),
                project_id=uuid4(),
                title="Should not persist",
                description=None,
            )

        note_repo.add.assert_not_called()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestCreateNoteValidation:
    def test_invalid_category_raises(self):
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(membership_reader=membership)
        with pytest.raises(InvalidCategoryError):
            uc.execute(
                actor_id=uuid4(),
                project_id=uuid4(),
                title="Bad category",
                description=None,
                category="reminder",
            )

    def test_empty_title_raises_value_error(self):
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(membership_reader=membership)
        with pytest.raises(ValueError):
            uc.execute(
                actor_id=uuid4(),
                project_id=uuid4(),
                title="   ",
                description=None,
            )

    def test_title_too_long_raises_value_error(self):
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(membership_reader=membership)
        with pytest.raises(ValueError):
            uc.execute(
                actor_id=uuid4(),
                project_id=uuid4(),
                title="A" * 201,
                description=None,
            )
