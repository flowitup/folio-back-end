"""SQLAlchemy ORM model for invoice_number_counters.

One row per (project, number prefix) — the prefix carries the tag and year,
e.g. "INV-2026-" or "FR-2026-". next_value is the next suffix to issue, so a
number is never handed out twice: deleting the latest expense does not free
its number, and concurrent creates serialize on the row instead of colliding
on uq_project_invoice_number.
"""

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.infrastructure.database.models.base import Base


class InvoiceNumberCounterModel(Base):
    """ORM model for invoice_number_counters (composite PK project_id, prefix)."""

    __tablename__ = "invoice_number_counters"

    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    prefix = Column(String(20), primary_key=True, nullable=False)
    next_value = Column(Integer, nullable=False)

    def __repr__(self) -> str:
        return f"<InvoiceNumberCounter project={self.project_id} prefix={self.prefix} next={self.next_value}>"
