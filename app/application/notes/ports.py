"""Repository and session ports (Protocols) for the notes application layer."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol
from uuid import UUID

from app.domain.entities.note import Note


class NoteRepositoryPort(Protocol):
    """Persistence contract for the Note aggregate."""

    def find_by_id(self, note_id: UUID) -> Optional[Note]:
        """Return note by UUID, or None if not found."""
        ...

    def list_by_project(self, project_id: UUID) -> list[Note]:
        """Return all notes for a project ordered by due_date ASC."""
        ...

    def add(self, note: Note) -> None:
        """Insert a new note."""
        ...

    def save(self, note: Note) -> None:
        """Update an existing note."""
        ...

    def delete(self, note_id: UUID) -> None:
        """Delete a note by UUID."""
        ...


class NoteDismissalRepositoryPort(Protocol):
    """Persistence contract for note dismissals (per-user, per-note)."""

    def add(self, user_id: UUID, note_id: UUID) -> None:
        """Record that a user dismissed a note notification."""
        ...

    def delete_all_for_note(self, note_id: UUID) -> None:
        """
        Delete all dismissal records for a note.

        Called by UpdateNoteUseCase when due_date or lead_time_minutes changes
        so that re-scheduled reminders fire again for all users.
        """
        ...

    def is_dismissed_by(self, user_id: UUID, note_id: UUID) -> bool:
        """Return True if the user has dismissed this note's notification."""
        ...


class ProjectMembershipReaderPort(Protocol):
    """Read-only membership check contract."""

    def is_member(self, user_id: UUID, project_id: UUID) -> bool:
        """Return True if the user is an active member of the project."""
        ...


class NoteQueryPort(Protocol):
    """Read-side query port for cross-table note queries."""

    def list_due_for_user(self, user_id: UUID, now: datetime, limit: int = 100) -> list[Note]:
        """
        Return open notes whose fire_at timestamp has passed for *user_id*,
        excluding notes the user has already dismissed.

        Hard cap: at most *limit* results (default 100). The underlying SQL
        should enforce LIMIT to protect the polling endpoint from unbounded results.

        SQL semantics (delegated fully to infrastructure):
            SELECT n.*
            FROM notes n
            INNER JOIN project_memberships m ON m.project_id = n.project_id
            WHERE m.user_id = :user_id
              AND n.status = 'open'
              AND (combine(due_date, '09:00') AT TIME ZONE 'UTC'
                   - lead_time_minutes * INTERVAL '1 minute') <= :now
              AND NOT EXISTS (
                  SELECT 1 FROM notes_dismissed d
                  WHERE d.user_id = :user_id AND d.note_id = n.id
              )
            ORDER BY n.due_date ASC
            LIMIT :limit
        """
        ...


# Re-export TransactionalSessionPort from invitations to avoid duplication.
# Both modules share the same minimal session contract (begin_nested + commit).
from app.application.invitations.ports import TransactionalSessionPort as TransactionalSessionPort  # noqa: E402,F401
