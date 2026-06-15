"""Pydantic v2 schemas for worker rate-change API endpoints."""

from datetime import date
from typing import List

from pydantic import BaseModel, Field


class CreateRateChangeRequest(BaseModel):
    """Request body for POST .../rate-changes."""

    effective_date: date
    daily_rate: float = Field(..., gt=0, description="Daily rate in currency units; must be > 0")


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
