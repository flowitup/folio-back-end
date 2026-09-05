"""Unit tests — SubmitOwnAttendanceUseCase date window (server-UTC based)."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.application.labor import SubmitOwnAttendanceRequest, SubmitOwnAttendanceUseCase
from app.domain.entities.worker import Worker
from app.domain.exceptions.labor_exceptions import AttendanceDateOutOfRangeError, WorkerNotLinkedError

PROJECT_ID = uuid4()
USER_ID = uuid4()
NOON = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
LATE = datetime(2026, 9, 5, 23, 0, tzinfo=timezone.utc)


def _worker():
    return Worker(
        id=uuid4(),
        project_id=PROJECT_ID,
        name="W",
        daily_rate=Decimal("100"),
        created_at=datetime.now(timezone.utc),
        user_id=USER_ID,
    )


def _usecase(worker):
    workers = Mock()
    workers.find_by_project_and_user.return_value = worker
    entries = Mock()
    entries.create.side_effect = lambda e: e
    return SubmitOwnAttendanceUseCase(worker_repo=workers, entry_repo=entries), entries


def _req(day, now):
    return SubmitOwnAttendanceRequest(project_id=PROJECT_ID, user_id=USER_ID, date=day, shift_type="full", now=now)


def test_today_and_yesterday_accepted():
    uc, entries = _usecase(_worker())
    for day in (date(2026, 9, 5), date(2026, 9, 4)):
        assert uc.execute(_req(day, NOON)).status == "pending"
    assert entries.create.call_count == 2


def test_two_days_back_rejected_with_window_in_message():
    uc, _ = _usecase(_worker())
    with pytest.raises(AttendanceDateOutOfRangeError) as exc:
        uc.execute(_req(date(2026, 9, 3), NOON))
    assert "between 2026-09-04 and 2026-09-05" in str(exc.value)


def test_tomorrow_rejected_at_midday_but_accepted_late_in_the_utc_day():
    uc, _ = _usecase(_worker())
    tomorrow = date(2026, 9, 6)
    with pytest.raises(AttendanceDateOutOfRangeError):
        uc.execute(_req(tomorrow, NOON))
    # 23:00 UTC is 06:00 the next morning in Vietnam: the phone is already on "tomorrow".
    assert uc.execute(_req(tomorrow, LATE)).date == "2026-09-06"


def test_unlinked_user_raises():
    uc, _ = _usecase(None)
    with pytest.raises(WorkerNotLinkedError):
        uc.execute(_req(date(2026, 9, 5), NOON))
