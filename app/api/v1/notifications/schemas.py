"""Pydantic v2 response schemas for the notifications API endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.api.v1.notes.schemas import NoteResponse


class DueNotificationResponse(BaseModel):
    """A single due notification — wraps a note with its fire_at timestamp."""

    model_config = ConfigDict(extra="forbid")

    note: NoteResponse
    # dismissed is always False in v1 (query filters them out).
    # Retained for forward-compatibility with a future "show recently dismissed" UI.
    dismissed: bool = False


class DueNotificationsListResponse(BaseModel):
    """Envelope for GET /api/v1/notifications — hard-capped at 100 items."""

    model_config = ConfigDict(extra="forbid")

    items: list[Any]  # list[DueNotificationResponse] — typed as Any to keep JSON-serialisable
    count: int
