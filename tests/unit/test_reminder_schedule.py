"""When scheduled reminders are due: Paris wall clock, DST, late runs, working days."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.domain.notifications.reminder_schedule import (
    ReminderKind,
    due_reminders,
    french_public_holidays,
    is_working_day,
    previous_month,
)

ATT = ReminderKind.ATTENDANCE
OVERDUE = ReminderKind.OVERDUE_FACTURES
MONTHLY = ReminderKind.MONTHLY_SUMMARY


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "moment, expected",
    [
        # Winter (CET, UTC+1): 19:30 Paris is 18:30 UTC.
        (utc(2026, 11, 23, 18, 29), []),
        (utc(2026, 11, 23, 18, 30), [ATT]),
        # A late run still sends; after 22:00 Paris the day is over.
        (utc(2026, 11, 23, 18, 45), [ATT]),
        (utc(2026, 11, 23, 20, 59), [ATT]),
        (utc(2026, 11, 23, 21, 0), []),
        # Summer (CEST, UTC+2): 19:30 Paris is 17:30 UTC; 18:30 UTC is 20:30 Paris.
        (utc(2026, 6, 16, 17, 29), []),
        (utc(2026, 6, 16, 17, 30), [ATT]),
        (utc(2026, 6, 16, 19, 59), [ATT]),
        (utc(2026, 6, 16, 20, 0), []),
        # The Monday right after the spring change.
        (utc(2026, 3, 30, 17, 35), [ATT]),
        (utc(2026, 3, 30, 18, 35), [ATT]),
    ],
)
def test_attendance_window_follows_paris_time(moment, expected):
    assert due_reminders(moment).kinds == expected


def test_no_attendance_reminder_on_weekends_or_public_holidays():
    assert due_reminders(utc(2026, 11, 21, 18, 35)).kinds == []  # Saturday
    assert due_reminders(utc(2026, 11, 22, 18, 35)).kinds == []  # Sunday
    assert due_reminders(utc(2026, 11, 11, 18, 35)).kinds == []  # Armistice, a Wednesday
    assert due_reminders(utc(2026, 4, 6, 17, 35)).kinds == []  # Easter Monday 2026


def test_overdue_factures_on_monday_morning_only():
    assert due_reminders(utc(2026, 11, 23, 7, 59)).kinds == []  # 08:59 Paris
    assert due_reminders(utc(2026, 11, 23, 8, 5)).kinds == [OVERDUE]
    assert due_reminders(utc(2026, 11, 23, 16, 59)).kinds == [OVERDUE]  # 17:59, late but same day
    assert due_reminders(utc(2026, 11, 23, 17, 0)).kinds == []  # 18:00
    assert due_reminders(utc(2026, 11, 24, 8, 5)).kinds == []  # Tuesday


def test_month_summary_on_the_first():
    assert due_reminders(utc(2026, 12, 1, 8, 5)).kinds == [MONTHLY]
    assert due_reminders(utc(2026, 12, 2, 8, 5)).kinds == []
    # 1 June 2026 is a Monday: both morning reminders are due together.
    assert due_reminders(utc(2026, 6, 1, 7, 5)).kinds == [OVERDUE, MONTHLY]


def test_the_paris_day_not_the_utc_day_decides():
    # 23:30 UTC on 30 November is already 00:30 on 1 December in Paris: not 09:00 yet.
    assert due_reminders(utc(2026, 11, 30, 23, 30)).today == date(2026, 12, 1)
    assert due_reminders(utc(2026, 11, 30, 23, 30)).kinds == []


def test_holidays_and_working_days():
    holidays = french_public_holidays(2026)
    assert date(2026, 4, 6) in holidays  # Easter Monday (Easter 5 April 2026)
    assert date(2026, 5, 14) in holidays  # Ascension
    assert date(2026, 5, 25) in holidays  # Whit Monday
    assert date(2026, 12, 25) in holidays
    assert is_working_day(date(2026, 11, 23))
    assert not is_working_day(date(2026, 11, 22))


def test_previous_month_wraps_the_year():
    assert previous_month(date(2027, 1, 1)) == (2026, 12)
    assert previous_month(date(2026, 12, 1)) == (2026, 11)
