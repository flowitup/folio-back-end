"""Unit tests for CreateNoteUseCase — mocked repositories."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.notes.create_note_usecase import CreateNoteUseCase
from app.application.notes.exceptions import InvalidLeadTimeError, NotProjectMemberError


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
            due_date=date(2026, 5, 1),
            lead_time_minutes=0,
        )

        assert dto.title == "Fix the roof"
        assert dto.description == "urgent"
        assert dto.due_date == date(2026, 5, 1)
        assert dto.lead_time_minutes == 0
        assert dto.status == "open"
        assert dto.project_id == project_id
        assert dto.created_by == actor_id

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
            due_date=date(2026, 5, 1),
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
            due_date=date(2026, 5, 1),
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
            due_date=date(2026, 5, 1),
        )

        assert dto.description is None

    def test_lead_time_60_accepted(self):
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(membership_reader=membership)
        dto = uc.execute(
            actor_id=uuid4(),
            project_id=uuid4(),
            title="Lead 60",
            description=None,
            due_date=date(2026, 5, 1),
            lead_time_minutes=60,
        )

        assert dto.lead_time_minutes == 60

    def test_lead_time_1440_accepted(self):
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(membership_reader=membership)
        dto = uc.execute(
            actor_id=uuid4(),
            project_id=uuid4(),
            title="Lead 1440",
            description=None,
            due_date=date(2026, 5, 1),
            lead_time_minutes=1440,
        )

        assert dto.lead_time_minutes == 1440

    def test_past_due_date_allowed(self):
        """Past due_date is deliberately allowed (user forgot to set it earlier)."""
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(membership_reader=membership)
        dto = uc.execute(
            actor_id=uuid4(),
            project_id=uuid4(),
            title="Old note",
            description=None,
            due_date=date(2020, 1, 1),
        )

        assert dto.due_date == date(2020, 1, 1)

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
            due_date=date(2026, 5, 1),
        )

        assert len(dto.title) == 200

    def test_fire_at_computed_correctly(self):
        """fire_at for lead_time=0 must be 09:00 UTC on due_date."""
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(membership_reader=membership)
        dto = uc.execute(
            actor_id=uuid4(),
            project_id=uuid4(),
            title="Fire at check",
            description=None,
            due_date=date(2026, 5, 1),
            lead_time_minutes=0,
        )

        expected = datetime(2026, 5, 1, 9, 0, 0, tzinfo=timezone.utc)
        assert dto.fire_at == expected


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
                due_date=date(2026, 5, 1),
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
            due_date=date(2026, 5, 1),
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
                due_date=date(2026, 5, 1),
            )

        note_repo.add.assert_not_called()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestCreateNoteValidation:
    def test_invalid_lead_time_raises(self):
        membership = MagicMock()
        membership.is_member.return_value = True

        uc = _make_usecase(membership_reader=membership)
        with pytest.raises(InvalidLeadTimeError):
            uc.execute(
                actor_id=uuid4(),
                project_id=uuid4(),
                title="Bad lead time",
                description=None,
                due_date=date(2026, 5, 1),
                lead_time_minutes=30,
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
                due_date=date(2026, 5, 1),
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
                due_date=date(2026, 5, 1),
            )
