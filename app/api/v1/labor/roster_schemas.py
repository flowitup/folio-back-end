"""Pydantic v2 schemas for the day-roster API endpoint (D3).

RosterRowResponse is an explicit whitelist — worker_id, name, status, hours,
day_type only. Never add rate/cost/amount/daily_rate/total here: the roster
is the one endpoint a plain "member" company role can read, and it must
never leak money (see plan 260908-0022-roles-permissions-redesign, D3).
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class RosterQuery(BaseModel):
    """Query-string schema for GET /projects/<id>/labor/roster."""

    date: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Day to render the roster for. Format: YYYY-MM-DD",
    )


class RosterRowResponse(BaseModel):
    """One worker's day — the D3 whitelist. Never rate/cost/amount/total."""

    worker_id: str
    name: str
    status: Literal["present", "pending", "absent"]
    hours: float
    day_type: Optional[str] = None


class RosterResponse(BaseModel):
    """Response body for GET /projects/<id>/labor/roster."""

    rows: List[RosterRowResponse]
