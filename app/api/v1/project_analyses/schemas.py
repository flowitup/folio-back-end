"""Pydantic v2 schemas for the project analyses API layer."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ListQueryParams(BaseModel):
    """Query params for GET /api/v1/projects/<id>/analyses."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    q: Optional[str] = Field(default=None, max_length=300)
    tag: list[str] = Field(default_factory=list)
    sort: Literal["created_at", "title"] = "created_at"
    order: Literal["asc", "desc"] = "desc"
    page: int = Field(default=1, ge=1, le=10_000)  # cap OFFSET DoS surface
    per_page: int = Field(default=25, ge=1, le=100)


class AnalysisUpdateBody(BaseModel):
    """Request body for PATCH /api/v1/projects/<id>/analyses/<id>.

    Every field is optional — the route only forwards fields present in the
    raw JSON payload (via ``model_fields_set``) so an omitted field leaves the
    corresponding entity attribute untouched (see UpdateProjectAnalysisUseCase
    docstring for the landmine this avoids).
    """

    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    summary: Optional[str] = Field(default=None, max_length=2000)
    source_url: Optional[str] = Field(default=None, max_length=2048)
    tags: Optional[list[str]] = Field(default=None, max_length=20)
