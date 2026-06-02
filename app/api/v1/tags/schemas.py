"""Pydantic v2 request/response schemas for the tags endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TagCreateBody(BaseModel):
    """Request body for POST /projects/<pid>/tags."""

    name: str = Field(..., min_length=1, max_length=100)
    color: str = Field(..., pattern=r"^#[0-9a-fA-F]{6}$")


class TagUpdateBody(BaseModel):
    """Request body for PUT /projects/<pid>/tags/<tag_id>."""

    name: str | None = Field(None, min_length=1, max_length=100)
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
