"""Pydantic v2 request/response schemas for the notes API endpoints."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NoteCreateBody(BaseModel):
    """Request body for POST /api/v1/projects/<id>/notes."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    due_date: date
    lead_time_minutes: Literal[0, 60, 1440] = 0


class NoteUpdateBody(BaseModel):
    """Request body for PATCH /api/v1/projects/<id>/notes/<id>."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    lead_time_minutes: Literal[0, 60, 1440] | None = None
    status: Literal["open", "done"] | None = None


class NoteResponse(BaseModel):
    """Response shape for a single note — returned by create, update, and get."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    project_id: UUID
    created_by: UUID
    title: str
    description: str | None
    due_date: date
    lead_time_minutes: int
    status: Literal["open", "done"]
    fire_at: datetime
    created_at: datetime
    updated_at: datetime
