"""Permission database model."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.associations import role_permissions


class PermissionModel(Base):
    """Permission database model."""

    __tablename__ = "permissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(100), unique=True, nullable=False)  # e.g., 'project:create'
    resource = Column(String(50), nullable=False)  # e.g., 'project'
    action = Column(String(50), nullable=False)  # e.g., 'create'
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    roles = relationship("RoleModel", secondary=role_permissions, back_populates="permissions")

    __table_args__ = (Index("ix_permissions_resource_action", "resource", "action"),)

    def __repr__(self) -> str:
        return f"<Permission {self.name}>"
