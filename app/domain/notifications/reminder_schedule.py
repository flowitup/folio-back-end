"""When the scheduled reminders are due, on the business calendar (Europe/Paris).

Owner decision QĐ-9: managers hear "no attendance logged today" at 19:30 on working days;
company admins hear about overdue factures on Monday morning and get a month summary on
the 1st. The job runs every 15 minutes from host cron (UTC), so every rule here reads the
Paris wall clock: daylight saving moves the UTC hour, never the reminder.

Each reminder has a window rather than an instant, so a late or skipped cron run still
sends it (19:45 still sends the 19:30 reminder) while a run long after the moment does not
(after 22:00 "you have not logged today" is no use any more). Idempotency across runs is
the sent log's job, not this module's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import List, Optional

from app.domain.time import business_now


class ReminderKind(str, Enum):
    ATTENDANCE = "reminder_attendance"
    OVERDUE_FACTURES = "reminder_overdue_factures"
    MONTHLY_SUMMARY = "reminder_monthly_summary"


#: Lower sends first when two reminders compete for a person's one push of the day.
PRIORITY = {
    ReminderKind.ATTENDANCE: 0,
    ReminderKind.OVERDUE_FACTURES: 1,
    ReminderKind.MONTHLY_SUMMARY: 2,
}


@dataclass(frozen=True)
class ReminderWindow:
    start: time
    end: time  # exclusive

    def contains(self, moment: time) -> bool:
        return self.start <= moment < self.end


ATTENDANCE_WINDOW = ReminderWindow(time(19, 30), time(22, 0))
# The morning reminders tolerate a cron outage of most of the day, but not an evening push.
MORNING_WINDOW = ReminderWindow(time(9, 0), time(18, 0))

#: How far back a project counts as "logging attendance" for the attendance reminder.
ACTIVE_DAYS = 7


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher), as the apps' holiday calendar."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return date(year, month, day + 1)


def french_public_holidays(year: int) -> set[date]:
    """Jours fériés (metropolitan France), the same days the web and mobile calendars mark."""
    easter = _easter_sunday(year)
    fixed = [(1, 1), (5, 1), (5, 8), (7, 14), (8, 15), (11, 1), (11, 11), (12, 25)]
    days = {date(year, m, d) for m, d in fixed}
    days |= {easter + timedelta(days=n) for n in (1, 39, 50)}  # Easter Monday, Ascension, Whit Monday
    return days


def is_working_day(day: date) -> bool:
    """Monday to Friday and not a French public holiday."""
    return day.weekday() < 5 and day not in french_public_holidays(day.year)


def previous_month(day: date) -> tuple[int, int]:
    """(year, month) of the month before ``day``'s."""
    first = day.replace(day=1) - timedelta(days=1)
    return first.year, first.month


@dataclass(frozen=True)
class DueReminders:
    """What a run at ``local_now`` should send."""

    local_now: datetime
    kinds: List[ReminderKind]

    @property
    def today(self) -> date:
        return self.local_now.date()


def due_reminders(at: Optional[datetime] = None) -> DueReminders:
    """The reminders due at ``at`` (default: now), read on the Paris wall clock.

    - attendance: working days, 19:30 to 22:00
    - overdue factures: Mondays, 09:00 to 18:00
    - month summary: the 1st of the month, 09:00 to 18:00
    """
    local = business_now(at)
    clock = local.time()
    day = local.date()
    kinds: List[ReminderKind] = []
    if is_working_day(day) and ATTENDANCE_WINDOW.contains(clock):
        kinds.append(ReminderKind.ATTENDANCE)
    if day.weekday() == 0 and MORNING_WINDOW.contains(clock):
        kinds.append(ReminderKind.OVERDUE_FACTURES)
    if day.day == 1 and MORNING_WINDOW.contains(clock):
        kinds.append(ReminderKind.MONTHLY_SUMMARY)
    return DueReminders(local_now=local, kinds=kinds)
