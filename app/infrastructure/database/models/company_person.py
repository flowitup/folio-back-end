"""SQLAlchemy ORM model for company_persons table.

The company-specific profile of a global `Person` (Phase 2 of the roles &
permissions redesign): a person can have one row per company they are
attached to (rate, labor role, onboarding state), while their identity
(name, phone) stays global on `persons`. Phone is a matching hint, never a
global unique key — the family-device invariant on `persons.phone` is
unaffected, and `phone_normalized` here is only unique WITHIN a company.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base


class CompanyPersonModel(Base):
    """ORM model for company_persons.

    One row per (company, person): the company-scoped profile used for
    onboarding, roster display, and default pay rate. `pending_expires_at`
    is set when the row was created ahead of the person having a linked
    user account (admin added them by phone before they signed up); a
    non-expired pending row is what `VerifySignupOtpUseCase` (Phase 2
    onboarding slice) looks for when linking a fresh sign-up by phone.
    """

    __tablename__ = "company_persons"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    person_id = Column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
    )
    labor_role_id = Column(
        UUID(as_uuid=True),
        ForeignKey("labor_roles.id", ondelete="SET NULL"),
        nullable=True,
    )
    default_daily_rate = Column(Numeric(10, 2), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="TRUE")
    # Matching hint only — set from the person's global phone (or the phone
    # the admin typed when adding this person to the company). Unique WITHIN
    # a company (partial unique index, declared in the migration — the
    # postgresql_where clause is not SQLite-compatible so it is omitted here,
    # same convention as persons.ix_persons_phone).
    phone_normalized = Column(String(32), nullable=True)
    pending_expires_at = Column(DateTime(timezone=True), nullable=True)
    # Company role chosen for a pending person, applied at signup (see CompanyPerson).
    pending_company_role = Column(String(16), nullable=True)
    created_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    company = relationship("CompanyModel", foreign_keys=[company_id])
    person = relationship("PersonModel", foreign_keys=[person_id])
    labor_role = relationship("LaborRoleModel", foreign_keys=[labor_role_id])
    created_by = relationship("UserModel", foreign_keys=[created_by_user_id])

    __table_args__ = (
        UniqueConstraint("company_id", "person_id", name="uq_company_persons_company_person"),
        Index("ix_company_persons_company_id", "company_id"),
        # A phone is unique within a company (migration 2ca24be9e3a8), on SQLite too.
        Index(
            "ix_company_persons_company_phone_unique",
            "company_id",
            "phone_normalized",
            unique=True,
            postgresql_where=text("phone_normalized IS NOT NULL"),
            sqlite_where=text("phone_normalized IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<CompanyPerson company={self.company_id} person={self.person_id}>"
