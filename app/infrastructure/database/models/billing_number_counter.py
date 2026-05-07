"""SQLAlchemy ORM model for billing_number_counters table.

Schema change in companies module (phase 03):
  Old PK: (user_id, kind, year)   FK → users.id CASCADE
  New PK: (company_id, kind, year) FK → companies.id CASCADE

Each legal entity now has its own continuous billing sequence per kind/year,
matching French accounting expectations for multi-entity operations.
"""

from sqlalchemy import Column, Enum, ForeignKey, Integer, SmallInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base


class BillingNumberCounterModel(Base):
    """ORM model for billing_number_counters.

    Composite PK (company_id, kind, year). next_value starts at 1.
    Rows are locked with SELECT FOR UPDATE by the counter repository to
    guarantee atomic sequence generation under concurrent document creates.
    """

    __tablename__ = "billing_number_counters"

    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
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

    company = relationship("CompanyModel", foreign_keys=[company_id])

    def __repr__(self) -> str:
        return (
            f"<BillingNumberCounter company={self.company_id} "
            f"kind={self.kind} year={self.year} next={self.next_value}>"
        )
