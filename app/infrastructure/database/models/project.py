"""Project database model."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.associations import user_projects


class ProjectModel(Base):
    """Project database model."""

    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(255), nullable=False)
    address = Column(String(500), nullable=True)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    # Company FK is nullable during the Phase 1/2 rollout: migration
    # b1c2d3e4f5a6 (and the follow-up backfill 15c1df3fdbfa) fill it in from
    # the owner's primary company access row, but the seed DB still has a
    # handful of orphans — tightening to NOT NULL is deferred to Phase 4
    # once every environment is clean (see the plan's tenancy phases).
    # ondelete=RESTRICT (Phase 2, migration 2ca24be9e3a8): a project is a
    # company asset — deleting the owning company while projects still
    # reference it must fail loudly (admin re-assigns or deletes the
    # projects first), not silently orphan them the way SET NULL did.
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    invoice_prefix = Column(String(8), nullable=True)
    # Budget fields — both nullable; a project may have no budget set.
    budget = Column(Numeric(14, 2), nullable=True)
    budget_source = Column(String(120), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    owner = relationship("UserModel", foreign_keys=[owner_id])
    company = relationship("CompanyModel", foreign_keys=[company_id])
    # primaryjoin/secondaryjoin required because user_projects now has two FKs
    # to users (user_id + invited_by_user_id); we must pin to user_id only.
    users = relationship(
        "UserModel",
        secondary=user_projects,
        primaryjoin=id == user_projects.c.project_id,
        secondaryjoin="UserModel.id == user_projects.c.user_id",
        back_populates="projects",
        foreign_keys=[user_projects.c.project_id, user_projects.c.user_id],
    )

    def __repr__(self) -> str:
        return f"<Project {self.name}>"
