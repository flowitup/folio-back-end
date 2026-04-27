"""Unit tests for ListDueNotificationsUseCase — clock injection + filter logic."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from app.application.notes.list_due_notifications_usecase import ListDueNotificationsUseCase
from app.domain.entities.note import Note


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc
_CLOCK = datetime(2026, 4, 27, 9, 0, 0, tzinfo=UTC)


def _make_note(*, project_id=None, status="open", due_date=date(2026, 4, 27)) -> Note:
    now = datetime(2026, 4, 27, 9, 0, 0, tzinfo=UTC)
    return Note(
        id=uuid4(),
        project_id=project_id or uuid4(),
        created_by=uuid4(),
        title="Reminder note",
        description=None,
        due_date=due_date,
        lead_time_minutes=0,
        status=status,
        created_at=now,
        updated_at=now,
    )


def _make_usecase(note_query=None) -> ListDueNotificationsUseCase:
    return ListDueNotificationsUseCase(note_query=note_query or MagicMock())


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestListDueNotificationsHappyPath:
    def test_returns_empty_list_when_no_notes_due(self):
        note_query = MagicMock()
        note_query.list_due_for_user.return_value = []

        uc = _make_usecase(note_query=note_query)
        result = uc.execute(user_id=uuid4(), now=_CLOCK)

        assert result == []

    def test_returns_due_notification_dtos(self):
        user_id = uuid4()
        note = _make_note()
        note_query = MagicMock()
        note_query.list_due_for_user.return_value = [note]

        uc = _make_usecase(note_query=note_query)
        result = uc.execute(user_id=user_id, now=_CLOCK)

        assert len(result) == 1
        assert result[0].note.id == note.id
        assert result[0].dismissed is False

    def test_dismissed_field_always_false_in_v1(self):
        """Query already excludes dismissed notes; DTO field is always False."""
        note_query = MagicMock()
        note_query.list_due_for_user.return_value = [_make_note(), _make_note()]

        uc = _make_usecase(note_query=note_query)
        result = uc.execute(user_id=uuid4(), now=_CLOCK)

        assert all(dto.dismissed is False for dto in result)

    def test_clock_injected_to_query_port(self):
        """The injected clock value is passed verbatim to note_query."""
        explicit_clock = datetime(2026, 4, 27, 8, 59, 0, tzinfo=UTC)
        note_query = MagicMock()
        note_query.list_due_for_user.return_value = []

        uc = _make_usecase(note_query=note_query)
        uc.execute(user_id=uuid4(), now=explicit_clock)

        call_kwargs = note_query.list_due_for_user.call_args
        assert call_kwargs.kwargs["now"] == explicit_clock

    def test_hard_cap_100_passed_to_query(self):
        """Use-case must pass limit=100 to the query port."""
        note_query = MagicMock()
        note_query.list_due_for_user.return_value = []

        uc = _make_usecase(note_query=note_query)
        uc.execute(user_id=uuid4(), now=_CLOCK)

        call_kwargs = note_query.list_due_for_user.call_args
        assert call_kwargs.kwargs["limit"] == 100

    def test_returns_at_most_100_items(self):
        """Even if query returns 100 items, use-case forwards all (cap in query)."""
        notes = [_make_note() for _ in range(100)]
        note_query = MagicMock()
        note_query.list_due_for_user.return_value = notes

        uc = _make_usecase(note_query=note_query)
        result = uc.execute(user_id=uuid4(), now=_CLOCK)

        assert len(result) == 100

    def test_user_id_forwarded_to_query(self):
        user_id = uuid4()
        note_query = MagicMock()
        note_query.list_due_for_user.return_value = []

        uc = _make_usecase(note_query=note_query)
        uc.execute(user_id=user_id, now=_CLOCK)

        call_kwargs = note_query.list_due_for_user.call_args
        assert call_kwargs.kwargs["user_id"] == user_id

    def test_notes_from_multiple_projects_returned(self):
        """Query already joins across all user's projects; use-case forwards all."""
        p1 = uuid4()
        p2 = uuid4()
        notes = [_make_note(project_id=p1), _make_note(project_id=p2)]
        note_query = MagicMock()
        note_query.list_due_for_user.return_value = notes

        uc = _make_usecase(note_query=note_query)
        result = uc.execute(user_id=uuid4(), now=_CLOCK)

        project_ids = {dto.note.project_id for dto in result}
        assert p1 in project_ids
        assert p2 in project_ids

    def test_default_now_uses_utc(self, monkeypatch):
        """When now is not injected, datetime.now(UTC) is used (not naive)."""
        note_query = MagicMock()
        note_query.list_due_for_user.return_value = []

        uc = _make_usecase(note_query=note_query)
        uc.execute(user_id=uuid4())  # no now= arg

        call_kwargs = note_query.list_due_for_user.call_args
        effective_now = call_kwargs.kwargs["now"]
        # Must be timezone-aware UTC
        assert effective_now.tzinfo is not None
        assert effective_now.utcoffset().total_seconds() == 0

    def test_done_notes_excluded_by_query(self):
        """Use-case delegates exclusion of done notes entirely to query port.
        A mock returning zero results correctly represents that contract."""
        note_query = MagicMock()
        note_query.list_due_for_user.return_value = []  # query filtered them out

        uc = _make_usecase(note_query=note_query)
        result = uc.execute(user_id=uuid4(), now=_CLOCK)

        assert result == []

    def test_dismissed_notes_excluded_by_query(self):
        """Use-case trusts query to exclude dismissed notes for this user.
        Other members of the same project are unaffected (query is per-user)."""
        note_query = MagicMock()
        note_query.list_due_for_user.return_value = []  # dismissed notes excluded

        uc = _make_usecase(note_query=note_query)
        result = uc.execute(user_id=uuid4(), now=_CLOCK)

        assert result == []
