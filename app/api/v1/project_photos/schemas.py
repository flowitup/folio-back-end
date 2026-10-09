"""Pydantic v2 schemas for the project photos API layer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Oldest capture date accepted. A value whose UTC time falls before year 1 is
# stored as a BC timestamp that psycopg2 cannot read back, which 500s every
# read of the project's photos; no real site photo predates this anyway.
MIN_CAPTURED_AT = datetime(1900, 1, 1, tzinfo=timezone.utc)
# Slack for clocks and timezones ahead of the server.
MAX_CAPTURED_AT_AHEAD = timedelta(days=1)
# Longest caption accepted, the same cap as the web and mobile caption fields.
MAX_CAPTION_LENGTH = 500


def bounded_captured_at(value: datetime) -> datetime:
    """Return ``value`` in UTC (naive = UTC), or raise ValueError when out of range."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    try:
        value = value.astimezone(timezone.utc)
    except OverflowError:
        raise ValueError("date is out of range") from None
    if value < MIN_CAPTURED_AT:
        raise ValueError("date must not be before 1900-01-01")
    if value > datetime.now(timezone.utc) + MAX_CAPTURED_AT_AHEAD:
        raise ValueError("date must not be in the future")
    return value


class ListQueryParams(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    page: int = Field(default=1, ge=1, le=10_000)
    per_page: int = Field(default=25, ge=1, le=100)


class UpdatePhotoBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    caption: Optional[str] = Field(default=None, max_length=MAX_CAPTION_LENGTH)
    captured_at: Optional[datetime] = None

    @field_validator("captured_at")
    @classmethod
    def _bound_captured_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        return None if value is None else bounded_captured_at(value)
