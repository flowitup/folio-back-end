"""SQLAlchemy ORM model for user_company_access table."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base


class UserCompanyAccessModel(Base):
    """ORM model for user_company_access.

    Many-to-many join between users and companies.
    Composite PK (user_id, company_id).
    At most one row per user may have is_primary=True, enforced by
    a partial unique index WHERE is_primary = TRUE in the DB.
    """

    __tablename__ = "user_company_access"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    is_primary = Column(Boolean, nullable=False, default=False, server_default="FALSE")
    attached_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user = relationship("UserModel", foreign_keys=[user_id])
    company = relationship("CompanyModel", foreign_keys=[company_id])

    __table_args__ = (
        # Regular index for company-side lookups (who has access to a company)
        Index("ix_user_company_access_company_id", "company_id"),
        # Partial unique is defined in the migration; declared here for reflection
        # completeness only — SQLAlchemy does not emit CREATE INDEX for this when
        # using create_all() because it lacks a postgresql_where expression on Index.
    )

    def __repr__(self) -> str:
        return f"<UserCompanyAccess user={self.user_id} " f"company={self.company_id} primary={self.is_primary}>"
