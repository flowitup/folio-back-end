"""Unit tests — the days a manager may log attendance for (single and bulk log)."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.application.labor import LogAttendanceRequest, LogAttendanceUseCase
from app.application.labor.attendance_dates import check_manager_attendance_date
from app.application.labor.bulk_log_attendance import (
    BulkLogAttendanceEntry,
    BulkLogAttendanceRequest,
    BulkLogAttendanceUseCase,
)
from app.domain.entities.worker import Worker
from app.domain.exceptions.labor_exceptions import AttendanceDateOutOfRangeError

PROJECT_ID = uuid4()
# 2026-09-05 14:00 in Paris.
NOON = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _worker():
    return Worker(
        id=uuid4(),
        project_id=PROJECT_ID,
        name="W",
        daily_rate=Decimal("100"),
        created_at=datetime.now(timezone.utc),
    )


def _log_usecase(worker):
    workers = Mock()
    workers.find_by_id.return_value = worker
    entries = Mock()
    entries.create.side_effect = lambda e: e
    return LogAttendanceUseCase(worker_repo=workers, entry_repo=entries), entries


@pytest.mark.parametrize(
    "day",
    [date(2026, 9, 5), date(2026, 9, 6), date(2026, 1, 2), date(2000, 1, 1)],
    ids=["today", "tomorrow (device ahead of the site)", "months back", "floor"],
)
def test_past_days_today_and_tomorrow_are_loggable(day):
    check_manager_attendance_date(day, NOON)


@pytest.mark.parametrize(
    "day",
    [date(2026, 9, 7), date(2031, 12, 25), date(1999, 12, 31), date(1900, 1, 1)],
    ids=["two days ahead", "years ahead", "before the floor", "1900"],
)
def test_far_future_and_ancient_days_are_refused_with_the_window(day):
    with pytest.raises(AttendanceDateOutOfRangeError) as exc:
        check_manager_attendance_date(day, NOON)
    assert "between 2000-01-01 and 2026-09-06" in str(exc.value)


def test_window_follows_the_site_calendar_not_utc():
    # 2026-09-05 23:30 UTC is already 2026-09-06 in Paris: the 7th becomes "tomorrow".
    late = datetime(2026, 9, 5, 23, 30, tzinfo=timezone.utc)
    check_manager_attendance_date(date(2026, 9, 7), late)


def test_single_log_refuses_a_future_day_before_saving():
    uc, entries = _log_usecase(_worker())
    with pytest.raises(AttendanceDateOutOfRangeError):
        uc.execute(
            LogAttendanceRequest(
                project_id=PROJECT_ID, worker_id=uuid4(), date=date(2031, 12, 25), shift_type="full", now=NOON
            )
        )
    entries.create.assert_not_called()


def test_single_log_accepts_today():
    worker = _worker()
    uc, entries = _log_usecase(worker)
    res = uc.execute(
        LogAttendanceRequest(
            project_id=PROJECT_ID, worker_id=worker.id, date=date(2026, 9, 5), shift_type="full", now=NOON
        )
    )
    assert res.date == "2026-09-05"
    entries.create.assert_called_once()


def test_bulk_log_refuses_an_ancient_day_before_touching_anything():
    workers = Mock()
    entries = Mock()
    db = Mock()
    uc = BulkLogAttendanceUseCase(worker_repo=workers, entry_repo=entries, db_session=db)
    with pytest.raises(AttendanceDateOutOfRangeError):
        uc.execute(
            BulkLogAttendanceRequest(
                project_id=PROJECT_ID,
                date=date(1900, 1, 1),
                entries=[BulkLogAttendanceEntry(worker_id=uuid4(), shift_type="full")],
                now=NOON,
            )
        )
    workers.find_by_id.assert_not_called()
    db.commit.assert_not_called()
