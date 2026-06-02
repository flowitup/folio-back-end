"""SQLAlchemy adapters implementing NoteRepositoryPort and NoteQueryPort."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.domain.entities.note import Note
from app.infrastructure.database.models.note_orm import NoteOrm


class SqlAlchemyNoteRepository:
    """Implements NoteRepositoryPort: CRUD for the Note aggregate.

    Also implements NoteQueryPort.list_due_for_user via a single textual SQL
    statement — no Python-side filtering, no N+1 queries.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # NoteRepositoryPort
    # ------------------------------------------------------------------

    def find_by_id(self, note_id: UUID) -> Optional[Note]:
        """Return Note by UUID, or None if not found."""
        orm = self._session.get(NoteOrm, note_id)
        return orm.to_entity() if orm is not None else None

    def find_by_id_for_update(self, note_id: UUID) -> Optional[Note]:
        """Return Note with SELECT FOR UPDATE row lock, or None if not found.

        Serializes concurrent updates against the same note row.
        Falls back to plain SELECT on dialects that don't support FOR UPDATE
        (e.g. SQLite in tests).
        """
        stmt = select(NoteOrm).where(NoteOrm.id == note_id).with_for_update()
        orm = self._session.execute(stmt).scalar_one_or_none()
        return orm.to_entity() if orm is not None else None

    def list_by_project(self, project_id: UUID) -> list[Note]:
        """Return all notes for a project ordered by created_at DESC."""
        rows = (
            self._session.query(NoteOrm)
            .filter(NoteOrm.project_id == project_id)
            .order_by(NoteOrm.created_at.desc())
            .all()
        )
        return [r.to_entity() for r in rows]

    def add(self, note: Note) -> None:
        """Insert a new note."""
        orm = NoteOrm.from_entity(note)
        self._session.add(orm)
        self._session.flush()

    def save(self, note: Note) -> None:
        """Update an existing note (merge state into session)."""
        orm = self._session.get(NoteOrm, note.id)
        if orm is None:
            raise ValueError(f"Note {note.id} not found — cannot save.")
        orm.update_from_entity(note)
        self._session.flush()

    def delete(self, note_id: UUID) -> None:
        """Delete a note by UUID. No-op if not found."""
        orm = self._session.get(NoteOrm, note_id)
        if orm is not None:
            self._session.delete(orm)
            self._session.flush()

    # ------------------------------------------------------------------
    # NoteQueryPort (dormant notifications path)
    # ------------------------------------------------------------------

    def list_due_for_user(self, user_id: UUID, now: datetime, limit: int = 100) -> list[Note]:
        """Return open notes with a passed fire_at for user_id.

        Legacy reminder rows only (due_date IS NOT NULL guard excludes new
        journal rows that have NULL due_date/status). Single SQL statement
        — no Python-side filtering, no N+1 queries.

        fire_at formula:
            (due_date::timestamp + TIME '09:00:00') AT TIME ZONE 'UTC'
            - (lead_time_minutes * INTERVAL '1 minute')
        """
        sql = text(
            """
            SELECT
                n.id,
                n.project_id,
                n.created_by,
                n.title,
                n.description,
                n.category,
                n.created_at,
                n.updated_at
            FROM notes n
            INNER JOIN user_projects m ON m.project_id = n.project_id
            WHERE m.user_id = :user_id
              AND n.status = 'open'
              AND n.due_date IS NOT NULL
              AND n.lead_time_minutes IS NOT NULL
              AND (
                (n.due_date::timestamp + TIME '09:00:00') AT TIME ZONE 'UTC'
                - (n.lead_time_minutes * INTERVAL '1 minute')
              ) <= :now
              AND NOT EXISTS (
                SELECT 1 FROM notes_dismissed d
                WHERE d.user_id = :user_id AND d.note_id = n.id
              )
            ORDER BY n.due_date ASC
            LIMIT :limit
            """
        )
        rows = self._session.execute(
            sql,
            {"user_id": str(user_id), "now": now, "limit": limit},
        ).fetchall()
        return [_row_to_entity(r) for r in rows]


def _row_to_entity(row: object) -> Note:
    """Convert a raw SQL row (from list_due_for_user) to a Note entity.

    SQLAlchemy Row supports positional access. We cast to Any once to avoid
    per-line ignores — the shape is guaranteed by the SELECT column list above.
    Legacy reminder rows have NULL status in the DB; treat as "open" on read
    (consistent with the ORM to_entity mapping).
    """
    r: Any = row
    return Note(
        id=r[0],
        project_id=r[1],
        created_by=r[2],
        title=r[3],
        description=r[4],
        category=r[5] if r[5] is not None else "general",
        status="open",
        created_at=r[6],
        updated_at=r[7],
    )
