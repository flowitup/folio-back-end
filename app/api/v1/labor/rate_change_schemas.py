"""Pydantic v2 schemas for worker rate-change API endpoints."""

from datetime import date
from typing import List

from pydantic import BaseModel, Field, field_validator

from app.api.v1.numeric_bounds import MAX_DAILY_AMOUNT, positive_cents


class CreateRateChangeRequest(BaseModel):
    """Request body for POST .../rate-changes."""

    effective_date: date
    daily_rate: float = Field(
        ..., gt=0, le=float(MAX_DAILY_AMOUNT), description="Daily rate in currency units; at least 0.01"
    )

    @field_validator("daily_rate")
    @classmethod
    def round_to_cents(cls, v: float) -> float:
        # The column keeps 2 decimals: 0.004 passed "> 0" and was then stored as 0.00.
        return positive_cents(v)


class RateChangeResponse(BaseModel):
    """Single rate-change response."""

    id: str
    worker_id: str
    effective_date: str  # ISO date string
    daily_rate: float
    created_at: str  # ISO datetime string


class RateChangeListResponse(BaseModel):
    """List of rate-change responses, ordered effective_date DESC."""

    rate_changes: List[RateChangeResponse]
