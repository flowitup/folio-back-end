"""Pydantic v2 schemas for the project documents API layer."""

from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ListQueryParams(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: list[Literal["pdf", "image", "spreadsheet", "doc", "cad", "text", "other"]] = Field(default_factory=list)
    uploader_id: Optional[UUID] = None
    sort: Literal["name", "size", "created_at", "uploader"] = "created_at"
    order: Literal["asc", "desc"] = "desc"
    page: int = Field(default=1, ge=1, le=10_000)  # cap OFFSET DoS surface (M2)
    per_page: int = Field(default=25, ge=1, le=100)
