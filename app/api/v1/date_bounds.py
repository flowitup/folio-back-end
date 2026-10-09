"""Bounds for business dates in request bodies.

An expense, a billing document or a task due date outside these years is a typo
("0026" for "2026") or an abuse, never a real record. Without a bound, year 9999
overflowed the "+30 days" default due date (a 500), year 1 gave document numbers
like "FAC-1-001", and the Expense page drew one bar per month between 1900 and
9999. Rate-change effective dates are deliberately not bounded (owner decision:
they can be backdated to any date); labor days have their own window
(app/application/labor/attendance_dates.py).
"""

from datetime import date
from typing import Annotated

from pydantic import AfterValidator

MIN_BUSINESS_DATE = date(2000, 1, 1)
MAX_BUSINESS_DATE = date(2100, 12, 31)


def check_business_date(value: date) -> date:
    """Return ``value`` unchanged, or raise ValueError when it is out of range."""
    if not MIN_BUSINESS_DATE <= value <= MAX_BUSINESS_DATE:
        raise ValueError(f"Date must be between {MIN_BUSINESS_DATE.isoformat()} and {MAX_BUSINESS_DATE.isoformat()}")
    return value


BusinessDate = Annotated[date, AfterValidator(check_business_date)]
