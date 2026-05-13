"""SQLAlchemy ORM model for persons table."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.models.base import Base


class PersonModel(Base):
    """ORM model for persons.

    Global identity for a physical human, decoupled from Project and
    Company. A single Person can have Worker rows in projects belonging
    to different companies (multi-company support).

    Scope and visibility are enforced at the application layer via
    Worker assignments + Project access permissions — there is no
    direct company FK on this table.

    Deduplication is human-reviewed via the merge tool (Phase 1c).
    Phone is intentionally NOT unique because two people may legitimately
    share a phone (family device).
    """

    __tablename__ = "persons"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(255), nullable=False)
    phone = Column(String(50), nullable=True)
    # Populated by the application layer as lower(trim(name)) on every
    # insert / update. Used for case-insensitive search and dedup hints.
    normalized_name = Column(String(255), nullable=False)

    # Audit: who first registered this Person. Not used for authorization.
    created_by_user_id = Column(
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
    creator = relationship("UserModel", foreign_keys=[created_by_user_id])
    workers = relationship(
        "WorkerModel",
        back_populates="person",
        # Don't cascade delete — workers must be merged off via the
        # merge tool before a person can be deleted (FK is RESTRICT).
        passive_deletes="all",
    )

    __table_args__ = (
        Index("ix_persons_normalized_name", "normalized_name"),
        Index("ix_persons_created_by", "created_by_user_id"),
        # NOTE: partial index on phone (WHERE phone IS NOT NULL) is
        # declared only in the Alembic migration (ix_persons_phone)
        # because the postgresql_where syntax is not SQLite-compatible.
        # Omitting it here keeps create_all() working in SQLite tests.
    )

    def __repr__(self) -> str:
        return f"<Person {self.id} name={self.name!r}>"
