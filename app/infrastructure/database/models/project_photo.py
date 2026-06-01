"""SQLAlchemy ORM model for project_photos — metadata for project-scoped progress photos."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.project_photo import ProjectPhoto
from app.infrastructure.database.models.base import Base


class ProjectPhotoRow(Base):
    """ORM model for project_photos.

    Content lives in object storage; this row holds metadata + storage keys.
    Soft-deletion is tracked via deleted_at (NULL = active, non-NULL = deleted).
    """

    __tablename__ = "project_photos"

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
    thumbnail_storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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
    def from_domain(cls, photo: ProjectPhoto) -> "ProjectPhotoRow":
        """Build an ORM row from a domain entity."""
        return cls(
            id=photo.id,
            project_id=photo.project_id,
            uploader_user_id=photo.uploader_user_id,
            filename=photo.filename,
            content_type=photo.content_type,
            size_bytes=photo.size_bytes,
            storage_key=photo.storage_key,
            thumbnail_storage_key=photo.thumbnail_storage_key,
            caption=photo.caption,
            captured_at=photo.captured_at,
            created_at=photo.created_at,
            deleted_at=photo.deleted_at,
        )

    def to_domain(self) -> ProjectPhoto:
        """Convert this ORM row to the corresponding domain entity."""
        return ProjectPhoto(
            id=self.id,
            project_id=self.project_id,
            uploader_user_id=self.uploader_user_id,
            filename=self.filename,
            content_type=self.content_type,
            size_bytes=self.size_bytes,
            storage_key=self.storage_key,
            thumbnail_storage_key=self.thumbnail_storage_key,
            caption=self.caption,
            captured_at=self.captured_at,
            created_at=self.created_at,
            deleted_at=self.deleted_at,
        )

    def __repr__(self) -> str:
        return f"<ProjectPhotoRow {self.id} '{self.filename}' project={self.project_id}>"
