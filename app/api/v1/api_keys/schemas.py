"""Pydantic v2 request schemas for the api-keys API endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CreateApiKeyBody(BaseModel):
    """Request body for POST /api/v1/api-keys."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
