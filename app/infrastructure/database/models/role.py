"""Role database model."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base
from app.infrastructure.database.models.associations import user_roles, role_permissions


class RoleModel(Base):
    """Role database model."""
    __tablename__ = "roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(50), unique=True, nullable=False)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    users = relationship("UserModel", secondary=user_roles, back_populates="roles")
    permissions = relationship(
        "PermissionModel", secondary=role_permissions, back_populates="roles"
    )

    def __repr__(self) -> str:
        return f"<Role {self.name}>"
