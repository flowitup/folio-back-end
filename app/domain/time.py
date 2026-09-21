"""The business calendar day.

Every date a human enters or reads in the product — an attendance day, the day a rate
change takes effect, "today" in a self-log window — is a date on the *site's* calendar,
not on UTC's. Resolving those with ``date.today()`` makes the product change day at
01:00 or 02:00 local time: between midnight and the UTC rollover a rate effective
"today" is still in the future, and "yesterday" is still two days back.

``business_today()`` is the one answer to "what day is it" for that kind of gate.
Timestamps (created_at, audit trails) stay in UTC: they record an instant, not a day.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

#: The calendar the business runs on. Single-tenant for now: every company is French.
BUSINESS_TZ = ZoneInfo("Europe/Paris")


def business_now(at: Optional[datetime] = None) -> datetime:
    """``at`` (default: now) as a wall clock in the business timezone.

    A naive ``at`` is read as UTC, matching how the app stores timestamps.
    """
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(BUSINESS_TZ)


def business_today(at: Optional[datetime] = None) -> date:
    """The business calendar date at ``at`` (default: now)."""
    return business_now(at).date()
