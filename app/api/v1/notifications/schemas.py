"""Pydantic v2 response schemas for the notifications API endpoints.

NOTE: These schemas are defined for documentation purposes and forward-compatibility.
The route currently builds its response inline (without these models) to avoid
double-serialisation. They are not imported by routes.py.
"""

from __future__ import annotations

from typing import Any  # noqa: F401

from pydantic import BaseModel, ConfigDict  # noqa: F401

from app.api.v1.notes.schemas import NoteResponse  # noqa: F401  # pragma: no cover


class DueNotificationResponse(BaseModel):  # pragma: no cover
    """A single due notification — wraps a note with its fire_at timestamp."""

    model_config = ConfigDict(extra="forbid")

    note: NoteResponse
    # dismissed is always False in v1 (query filters them out).
    # Retained for forward-compatibility with a future "show recently dismissed" UI.
    dismissed: bool = False


class DueNotificationsListResponse(BaseModel):  # pragma: no cover
    """Envelope for GET /api/v1/notifications — hard-capped at 100 items."""

    model_config = ConfigDict(extra="forbid")

    items: list[Any]  # list[DueNotificationResponse] — typed as Any to keep JSON-serialisable
    count: int
