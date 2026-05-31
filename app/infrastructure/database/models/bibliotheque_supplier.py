"""SQLAlchemy ORM model for bibliotheque_suppliers table."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities.supplier import Supplier
from app.infrastructure.database.models.base import Base


class BibliothequeSupplierModel(Base):
    """SQLAlchemy mapping for bibliotheque_suppliers.

    Company-scoped: slug is unique within a company, not globally.
    """

    __tablename__ = "bibliotheque_suppliers"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    website_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    product_url_template: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("company_id", "slug", name="uq_bibliotheque_suppliers_company_slug"),
        Index("ix_bibliotheque_suppliers_company_id", "company_id"),
    )

    def to_entity(self) -> Supplier:
        return Supplier(
            id=self.id,
            company_id=self.company_id,
            name=self.name,
            slug=self.slug,
            website_url=self.website_url,
            logo_url=self.logo_url,
            product_url_template=self.product_url_template,
            created_at=self.created_at,
        )

    @classmethod
    def from_entity(cls, s: Supplier) -> "BibliothequeSupplierModel":
        return cls(
            id=s.id,
            company_id=s.company_id,
            name=s.name,
            slug=s.slug,
            website_url=s.website_url,
            logo_url=s.logo_url,
            product_url_template=s.product_url_template,
            created_at=s.created_at,
        )

    def __repr__(self) -> str:
        return f"<BibliothequeSupplierModel {self.id} '{self.slug}' company={self.company_id}>"
