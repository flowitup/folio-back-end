"""SQLAlchemy ORM model for billing_number_counters table."""

from sqlalchemy import Column, Enum, ForeignKey, Integer, SmallInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base


class BillingNumberCounterModel(Base):
    """ORM model for billing_number_counters.

    Composite PK (user_id, kind, year). next_value starts at 1.
    Rows are locked with SELECT FOR UPDATE by the counter repository to
    guarantee atomic sequence generation under concurrent document creates.
    """

    __tablename__ = "billing_number_counters"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    kind = Column(
        Enum("devis", "facture", name="billing_document_kind", create_type=False),
        primary_key=True,
        nullable=False,
    )
    year = Column(SmallInteger, primary_key=True, nullable=False)
    next_value = Column(Integer, nullable=False, default=1, server_default="1")

    user = relationship("UserModel", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return (
            f"<BillingNumberCounter user={self.user_id} " f"kind={self.kind} year={self.year} next={self.next_value}>"
        )
