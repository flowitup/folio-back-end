"""SQLAlchemy ORM model for project_analyses — metadata for uploaded HTML reports."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.entities.project_analysis import ProjectAnalysis
from app.infrastructure.database.models.base import Base


class ProjectAnalysisTagRow(Base):
    __tablename__ = "project_analysis_tags"

    analysis_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("project_analyses.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag: Mapped[str] = mapped_column(String(100), primary_key=True)


class ProjectAnalysisModel(Base):
    """ORM model for project_analyses — report metadata only; the HTML body
    lives in object storage under ``storage_key``.

    Soft-deletion is tracked via deleted_at (NULL = active, non-NULL = deleted).
    """

    __tablename__ = "project_analyses"

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
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    _tags = relationship(
        "ProjectAnalysisTagRow",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    @classmethod
    def from_domain(cls, analysis: ProjectAnalysis) -> "ProjectAnalysisModel":
        """Build an ORM model instance from a domain entity."""
        model = cls(
            id=analysis.id,
            project_id=analysis.project_id,
            uploader_user_id=analysis.uploader_user_id,
            title=analysis.title,
            summary=analysis.summary,
            source_url=analysis.source_url,
            storage_key=analysis.storage_key,
            size_bytes=analysis.size_bytes,
            created_at=analysis.created_at,
            updated_at=analysis.updated_at,
            deleted_at=analysis.deleted_at,
        )
        model._tags = [ProjectAnalysisTagRow(analysis_id=analysis.id, tag=t) for t in analysis.tags]
        return model

    def to_domain(self) -> ProjectAnalysis:
        """Convert this ORM model to the corresponding domain entity."""
        return ProjectAnalysis(
            id=self.id,
            project_id=self.project_id,
            uploader_user_id=self.uploader_user_id,
            title=self.title,
            summary=self.summary,
            source_url=self.source_url,
            storage_key=self.storage_key,
            size_bytes=self.size_bytes,
            tags=tuple(sorted(row.tag for row in self._tags)),
            created_at=self.created_at,
            updated_at=self.updated_at,
            deleted_at=self.deleted_at,
        )

    def __repr__(self) -> str:
        return f"<ProjectAnalysisModel {self.id} '{self.title}' project={self.project_id}>"
