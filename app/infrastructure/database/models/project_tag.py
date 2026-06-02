"""ProjectTag database model — project-scoped phase/cost tag."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base


class ProjectTagModel(Base):
    """SQLAlchemy ORM model for the project_tags table."""

    __tablename__ = "project_tags"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_project_tags_project_id"),
        nullable=False,
        index=True,
    )
    name = Column(String(100), nullable=False)
    color = Column(String(7), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_project_tags_project_id_name"),)

    # Relationships (back-populated from labor_entries and invoices via tag_id)
    labor_entries = relationship(
        "LaborEntryModel",
        back_populates="tag",
        foreign_keys="LaborEntryModel.tag_id",
        passive_deletes=True,
    )
    invoices = relationship(
        "InvoiceModel",
        back_populates="tag",
        foreign_keys="InvoiceModel.tag_id",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<ProjectTag {self.name!r} project={self.project_id}>"
