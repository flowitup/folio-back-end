"""SQLAlchemy ORM models for notes and note dismissals.

Maps to the 'notes' and 'notes_dismissed' tables created in migration dfe858ecff3d.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.note import Note
from app.infrastructure.database.models.base import Base


class NoteOrm(Base):
    """SQLAlchemy mapping for the notes table."""

    __tablename__ = "notes"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    lead_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", server_default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def to_entity(self) -> Note:
        """Convert ORM model to domain entity."""
        return Note(
            id=self.id,
            project_id=self.project_id,
            created_by=self.created_by,
            title=self.title,
            description=self.description,
            due_date=self.due_date,
            lead_time_minutes=self.lead_time_minutes,
            status=self.status,  # type: ignore[arg-type]  # DB-enforced to be 'open'|'done'
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_entity(cls, note: Note) -> "NoteOrm":
        """Convert domain entity to ORM model."""
        return cls(
            id=note.id,
            project_id=note.project_id,
            created_by=note.created_by,
            title=note.title,
            description=note.description,
            due_date=note.due_date,
            lead_time_minutes=note.lead_time_minutes,
            status=note.status,
            created_at=note.created_at,
            updated_at=note.updated_at,
        )

    def update_from_entity(self, note: Note) -> None:
        """Mutate this ORM instance in-place from a domain entity (for save operations)."""
        self.title = note.title
        self.description = note.description
        self.due_date = note.due_date
        self.lead_time_minutes = note.lead_time_minutes
        self.status = note.status
        self.updated_at = note.updated_at

    def __repr__(self) -> str:
        return f"<NoteOrm {self.id} '{self.title}' status={self.status}>"


class NoteDismissalOrm(Base):
    """SQLAlchemy mapping for the notes_dismissed table (composite PK)."""

    __tablename__ = "notes_dismissed"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    note_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notes.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    dismissed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<NoteDismissalOrm user={self.user_id} note={self.note_id}>"
