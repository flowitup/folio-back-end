"""Pydantic v2 schemas for the assistant actions/audit API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
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


class AssistantAuditRow(BaseModel):
    """One row of GET /assistant/audit — the web supervision page's list item."""

    id: UUID
    created_at: datetime
    channel_key: str
    user_id: Optional[UUID]
    user_name: str
    intent: Optional[str]
    feature: Optional[str]
    outcome: Optional[str]
    refused_reason: Optional[str]
    cost_usd: Decimal
    trace_id: Optional[str]


class AssistantAuditListResponse(BaseModel):
    items: list[AssistantAuditRow]
