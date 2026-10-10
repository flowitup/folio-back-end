"""The days a manager may log attendance for.

A manager can backfill any past day (a worker added late, a forgotten week), but a day
in the future or before any site could exist is a typo that would still be priced and
paid: 2031-12-25 for 2025-12-25, or 1900-01-01 from a broken date field.
"""

from datetime import date, datetime, timedelta
from typing import Optional

from app.domain.exceptions.labor_exceptions import AttendanceDateOutOfRangeError
from app.domain.time import business_today

#: No project in the product predates this; an older day is a typo.
EARLIEST_ATTENDANCE_DATE = date(2000, 1, 1)

# Days past the site's "today" still accepted: a manager east of the site (Vietnam is
# UTC+7, the site UTC+1/+2) is already on tomorrow during the site's evening.
_FORWARD_TOLERANCE_DAYS = 1


def check_manager_attendance_date(day: date, now: Optional[datetime] = None) -> None:
    """Raise AttendanceDateOutOfRangeError unless ``day`` is loggable at ``now`` (default: now)."""
    latest = business_today(now) + timedelta(days=_FORWARD_TOLERANCE_DAYS)
    if not (EARLIEST_ATTENDANCE_DATE <= day <= latest):
        raise AttendanceDateOutOfRangeError(
            f"Attendance can only be logged between {EARLIEST_ATTENDANCE_DATE.isoformat()} and {latest.isoformat()}"
        )
