"""Data Transfer Objects for notes use-case results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from app.domain.entities.note import Note


@dataclass(frozen=True)
class NoteDto:
    """Read model returned by note use-cases."""

    id: UUID
    project_id: UUID
    created_by: UUID
    title: str
    description: str | None
    due_date: date
    lead_time_minutes: int
    status: Literal["open", "done"]
    fire_at: datetime
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, note: Note) -> NoteDto:
        """Build a NoteDto from a Note entity, computing fire_at inline."""
        return cls(
            id=note.id,
            project_id=note.project_id,
            created_by=note.created_by,
            title=note.title,
            description=note.description,
            due_date=note.due_date,
            lead_time_minutes=note.lead_time_minutes,
            status=note.status,
            fire_at=Note.fire_at(note.due_date, note.lead_time_minutes),
            created_at=note.created_at,
            updated_at=note.updated_at,
        )


@dataclass(frozen=True)
class DueNotificationDto:
    """
    A note whose reminder is due and has not been dismissed by the requesting user.

    ``dismissed`` is always False in v1 (the query filters dismissed notes out).
    The field is retained for forward-compatibility with a future "show recently
    dismissed" UI variant.
    """

    note: NoteDto
    dismissed: bool = False
