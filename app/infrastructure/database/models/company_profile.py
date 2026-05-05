"""SQLAlchemy ORM model for company_profile table."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base


class CompanyProfileModel(Base):
    """ORM model for company_profile.

    One row per user (user_id is both PK and FK to users).
    Stores the issuer's legal/banking info edited in Settings.
    A snapshot of relevant fields is copied onto each BillingDocument at
    create time — edits here do NOT mutate existing docs.
    """

    __tablename__ = "company_profile"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    legal_name = Column(String(255), nullable=False)
    address = Column(Text, nullable=False)
    siret = Column(String(32), nullable=True)
    tva_number = Column(String(32), nullable=True)
    iban = Column(String(64), nullable=True)
    bic = Column(String(32), nullable=True)
    logo_url = Column(Text, nullable=True)
    default_payment_terms = Column(Text, nullable=True)
    prefix_override = Column(String(8), nullable=True)

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

    user = relationship("UserModel", foreign_keys=[user_id])

    __table_args__ = (UniqueConstraint("user_id", name="uq_company_profile_user_id"),)

    def __repr__(self) -> str:
        return f"<CompanyProfile user={self.user_id} legal_name={self.legal_name!r}>"
