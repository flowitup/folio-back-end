"""Unit tests — the business calendar day (Europe/Paris), not UTC's."""

from datetime import date, datetime, timezone

from app.domain.time import business_now, business_today


def test_late_utc_evening_is_already_the_next_business_day():
    """23:30 UTC is 01:30 the next morning on site — the business day has rolled over.

    This is the whole point of the helper: between midnight on site and the UTC
    rollover, ``date.today()`` still answers yesterday.
    """
    assert business_today(datetime(2026, 9, 21, 23, 30, tzinfo=timezone.utc)) == date(2026, 9, 22)


def test_winter_offset_is_applied_too():
    """Paris is UTC+1 in January, so 23:30 UTC is 00:30 the next day."""
    assert business_today(datetime(2026, 1, 15, 23, 30, tzinfo=timezone.utc)) == date(2026, 1, 16)


def test_midday_utc_stays_on_the_same_day():
    assert business_today(datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)) == date(2026, 9, 21)


def test_naive_input_is_read_as_utc():
    """Timestamps are stored naive-UTC in places; they must not be read as local time."""
    assert business_today(datetime(2026, 9, 21, 23, 30)) == date(2026, 9, 22)


def test_business_now_returns_the_local_wall_clock():
    local = business_now(datetime(2026, 9, 21, 23, 30, tzinfo=timezone.utc))
    assert (local.hour, local.minute) == (1, 30)


def test_defaults_to_the_current_instant():
    assert isinstance(business_today(), date)
