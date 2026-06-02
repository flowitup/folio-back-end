"""Pydantic v2 schemas for the project photos API layer."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ListQueryParams(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    page: int = Field(default=1, ge=1, le=10_000)
    per_page: int = Field(default=25, ge=1, le=100)


class UpdatePhotoBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    caption: Optional[str] = None
    captured_at: Optional[datetime] = None
