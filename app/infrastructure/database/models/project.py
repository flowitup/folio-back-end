"""Project database model."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String
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
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    owner = relationship("UserModel", foreign_keys=[owner_id])
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
