"""Pydantic v2 request schemas for the notes API endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NoteCategory = Literal["inspection", "delivery", "payment", "decision", "call", "general"]


class NoteCreateBody(BaseModel):
    """Request body for POST /api/v1/projects/<id>/notes."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    category: NoteCategory = "general"


class NoteUpdateBody(BaseModel):
    """Request body for PATCH /api/v1/projects/<id>/notes/<id>."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    category: NoteCategory | None = None
