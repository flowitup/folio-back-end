"""SQLAlchemy ORM model for the payment_methods table.

Matches the Alembic migration ``cea9f050672d_add_payment_methods_and_invoice_columns``
column-for-column (types, nullability, FK on_delete behaviour).

SQLite compatibility note
--------------------------
The partial unique index ``ux_payment_methods_company_label_active`` is
expressed as raw SQL DDL in the migration (``op.execute``) because Alembic
cannot represent functional expressions portably. The ``__table_args__``
below therefore only declares the covering composite index and omits the
partial-unique definition — it is enforced by the migration on PostgreSQL only.
"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base


class PaymentMethodModel(Base):
    """ORM model for payment_methods.

    Stores reusable payment methods scoped per company. Soft-delete via
    ``is_active``; builtin rows (Cash, company legal name) are protected from
    deletion via ``is_builtin``.
    """

    __tablename__ = "payment_methods"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )

    label = Column(String(120), nullable=False)

    is_builtin = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)

    # NULL when the creating user has been hard-deleted (ON DELETE SET NULL)
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
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

    # Relationships (lazy-loaded, read-only)
    company = relationship("CompanyModel", foreign_keys=[company_id])
    creator = relationship("UserModel", foreign_keys=[created_by])

    __table_args__ = (
        # Covering composite index for the dominant query pattern:
        # "all (active) methods for a company"
        # The partial-unique functional index on (company_id, lower(label))
        # WHERE is_active = true is created directly via SQL in the migration
        # because SQLAlchemy cannot express functional partial indexes in a
        # SQLite-compatible way.
        Index("ix_payment_methods_company_active", "company_id", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<PaymentMethod {self.id} company={self.company_id} " f"label={self.label!r} active={self.is_active}>"
