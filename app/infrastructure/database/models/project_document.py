"""SQLAlchemy ORM model for project_documents — metadata for project-scoped file uploads."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.project_document import ProjectDocument
from app.infrastructure.database.models.base import Base


class ProjectDocumentModel(Base):
    """ORM model for project_documents — file metadata only; content lives in object storage.

    Soft-deletion is tracked via deleted_at (NULL = active, non-NULL = deleted).
    """

    __tablename__ = "project_documents"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default="gen_random_uuid()",
    )
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    uploader_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    @classmethod
    def from_domain(cls, doc: ProjectDocument) -> "ProjectDocumentModel":
        """Build an ORM model instance from a domain entity."""
        return cls(
            id=doc.id,
            project_id=doc.project_id,
            uploader_user_id=doc.uploader_user_id,
            filename=doc.filename,
            content_type=doc.content_type,
            size_bytes=doc.size_bytes,
            storage_key=doc.storage_key,
            created_at=doc.created_at,
            deleted_at=doc.deleted_at,
        )

    def to_domain(self) -> ProjectDocument:
        """Convert this ORM model to the corresponding domain entity."""
        return ProjectDocument(
            id=self.id,
            project_id=self.project_id,
            uploader_user_id=self.uploader_user_id,
            filename=self.filename,
            content_type=self.content_type,
            size_bytes=self.size_bytes,
            storage_key=self.storage_key,
            created_at=self.created_at,
            deleted_at=self.deleted_at,
        )

    def __repr__(self) -> str:
        return f"<ProjectDocumentModel {self.id} '{self.filename}' project={self.project_id}>"
