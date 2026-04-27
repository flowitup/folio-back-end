"""Pydantic v2 request schemas for the notes API endpoints."""

from __future__ import annotations

from datetime import date
from typing import Literal

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
