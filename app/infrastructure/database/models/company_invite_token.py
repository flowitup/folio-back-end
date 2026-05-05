"""SQLAlchemy ORM model for company_invite_tokens table."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base


class CompanyInviteTokenModel(Base):
    """ORM model for company_invite_tokens.

    Single-use invite tokens that allow users to attach themselves to a company.
    Only the argon2 hash of the plaintext token is stored.
    The plaintext is shown exactly once at generation time.

    At most one unredeemed token per company is enforced by a partial unique
    index WHERE redeemed_at IS NULL (declared in the migration).
    """

    __tablename__ = "company_invite_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    # argon2 hash of the plaintext token — plaintext is never stored
    token_hash = Column(Text, nullable=False)

    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)

    # NULL = not yet redeemed
    redeemed_at = Column(DateTime(timezone=True), nullable=True)
    redeemed_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    company = relationship("CompanyModel", foreign_keys=[company_id])
    creator = relationship("UserModel", foreign_keys=[created_by])
    redeemer = relationship("UserModel", foreign_keys=[redeemed_by])

    __table_args__ = (
        Index("ix_company_invite_tokens_company_id", "company_id"),
        # Partial unique uix_company_invite_tokens_active_per_company
        # WHERE redeemed_at IS NULL is defined in the migration only.
    )

    def __repr__(self) -> str:
        redeemed = self.redeemed_at is not None
        return f"<CompanyInviteToken {self.id} company={self.company_id} " f"redeemed={redeemed}>"
