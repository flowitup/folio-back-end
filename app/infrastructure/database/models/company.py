"""SQLAlchemy ORM model for companies table."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base


class CompanyModel(Base):
    """ORM model for companies.

    Stores shared legal entities managed by admins.
    Users attach to companies via invite tokens recorded in user_company_access.
    Sensitive fields (siret, tva_number, iban, bic) are stored raw here;
    masking is applied at the application layer read boundary.
    """

    __tablename__ = "companies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    legal_name = Column(String(255), nullable=False)
    address = Column(Text, nullable=False)

    # Sensitive financial identifiers
    siret = Column(String(32), nullable=True)
    tva_number = Column(String(32), nullable=True)
    iban = Column(String(64), nullable=True)
    bic = Column(String(32), nullable=True)

    # Branding
    logo_url = Column(Text, nullable=True)

    # Billing defaults (per-company)
    default_payment_terms = Column(Text, nullable=True)
    prefix_override = Column(String(8), nullable=True)

    # Audit
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    creator = relationship("UserModel", foreign_keys=[created_by])

    __table_args__ = (
        # NOTE: prefix_override format check (~ regex) is declared only in the
        # Alembic migration (ck_companies_prefix_override_format) because the
        # PostgreSQL ~ operator is not valid SQLite syntax. Omitting it here
        # prevents create_all() from failing in SQLite-backed test sessions.
        Index("ix_companies_legal_name", "legal_name"),
        Index("ix_companies_created_by", "created_by"),
    )

    def __repr__(self) -> str:
        return f"<Company {self.id} name={self.legal_name!r}>"
