"""Invitation database model — maps to the 'invitations' table (phase 01 migration)."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.infrastructure.database.models.base import Base
from app.domain.entities.invitation import Invitation, InvitationStatus


class InvitationModel(Base):
    """SQLAlchemy mapping for the invitations table."""

    __tablename__ = "invitations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    email = Column(Text, nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False)
    token_hash = Column(Text, nullable=False, unique=True)
    status = Column(String(20), nullable=False, default="pending")
    expires_at = Column(DateTime(timezone=True), nullable=False)
    invited_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    def to_entity(self) -> Invitation:
        """Convert ORM model to domain entity."""
        return Invitation(
            id=self.id,
            email=self.email,
            project_id=self.project_id,
            role_id=self.role_id,
            token_hash=self.token_hash,
            status=InvitationStatus(self.status),
            expires_at=self.expires_at,
            invited_by=self.invited_by,
            created_at=self.created_at,
            accepted_at=self.accepted_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_entity(cls, inv: Invitation) -> "InvitationModel":
        """Convert domain entity to ORM model."""
        return cls(
            id=inv.id,
            email=inv.email,
            project_id=inv.project_id,
            role_id=inv.role_id,
            token_hash=inv.token_hash,
            status=inv.status.value,
            expires_at=inv.expires_at,
            invited_by=inv.invited_by,
            created_at=inv.created_at,
            accepted_at=inv.accepted_at,
            updated_at=inv.updated_at,
        )

    def __repr__(self) -> str:
        return f"<Invitation {self.id} {self.email} {self.status}>"
