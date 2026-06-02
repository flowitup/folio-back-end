"""SQLAlchemy ORM models for notes and note dismissals.

Maps to the 'notes' and 'notes_dismissed' tables.
Legacy reminder columns (due_date, lead_time_minutes, status) are kept nullable
for backwards-compatibility with pre-journal rows and the dormant notifications
query; they are not mapped to the journal Note entity.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.note import Note
from app.infrastructure.database.models.base import Base


class NoteOrm(Base):
    """SQLAlchemy mapping for the notes table."""

    __tablename__ = "notes"
    __table_args__ = (
        # Index supporting journal list query (project_id + created_at DESC).
        Index("ix_notes_project_created", "project_id", "created_at"),
    )

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
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Journal category — required for all new notes; defaults to 'general'.
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="general", server_default="general")
    # Legacy reminder columns — retained for dormant notifications path.
    # Nullable so new journal rows do not need to supply them.
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    lead_time_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
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
            category=self.category or "general",
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_entity(cls, note: Note) -> "NoteOrm":
        """Convert domain entity to ORM model.

        Legacy reminder fields (due_date, lead_time_minutes, status) are left
        NULL — the entity no longer carries them.
        """
        return cls(
            id=note.id,
            project_id=note.project_id,
            created_by=note.created_by,
            title=note.title,
            description=note.description,
            category=note.category,
            created_at=note.created_at,
            updated_at=note.updated_at,
        )

    def update_from_entity(self, note: Note) -> None:
        """Mutate this ORM instance in-place from a domain entity (for save operations)."""
        self.title = note.title
        self.description = note.description
        self.category = note.category
        self.updated_at = note.updated_at

    def __repr__(self) -> str:
        return f"<NoteOrm {self.id} '{self.title}' category={self.category}>"


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
