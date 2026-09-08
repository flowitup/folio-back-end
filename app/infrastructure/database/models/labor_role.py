"""LaborRole database model."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.infrastructure.database.models.base import Base


class LaborRoleModel(Base):
    """SQLAlchemy model for the labor_roles table.

    Phase 2 (company-as-tenant): roles used to be global (`UNIQUE(name)`).
    `company_id` scopes a role to one company (nullable — legacy rows from
    before the backfill, or ambiguous multi-company data, stay NULL and are
    excluded from a company's list); `slug` gives clients a stable i18n key
    (e.g. "tho_chinh") instead of keying UI copy on a random UUID.
    """

    __tablename__ = "labor_roles"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    name = Column(String(100), nullable=False)
    slug = Column(String(40), nullable=True)
    color = Column(String(7), nullable=False)
    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime,
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=True,
    )

    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_labor_roles_company_name"),)

    def __repr__(self) -> str:
        return f"<LaborRole {self.name}>"
