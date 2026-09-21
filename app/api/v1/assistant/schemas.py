"""Pydantic v2 schemas for the assistant actions API."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SubmitActionBody(BaseModel):
    """JSON body of POST /assistant/actions (a tapped choice option)."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    reply_to_id: UUID


class ActionAcceptedResponse(BaseModel):
    accepted: bool
