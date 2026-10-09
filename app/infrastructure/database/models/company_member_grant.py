"""SQLAlchemy ORM model for company_member_grants table (D8).

Per-user permission customisation on top of the company-role matrix
(`app.domain.authz.matrix`): an admin can grant or deny one of the
`CUSTOMISABLE_PERMISSIONS` to a manager/member, company-wide (`project_id`
NULL) or scoped to one project. Read by
`app.infrastructure.database.repositories.sqlalchemy_authz_reader
.SqlAlchemyAuthzReader.grants_for` and folded into
`app.domain.authz.resolver.effective_permissions`
(``effective = (matrix[role] ∪ grants) − denies``).

Two uniqueness rules are needed because Postgres treats NULL as distinct in a
composite UNIQUE constraint — a plain
`UNIQUE(company_id, user_id, permission, project_id)` would let the SAME
company-wide row (`project_id IS NULL`) be inserted twice. The migration adds
a second partial unique index `(company_id, user_id, permission) WHERE
project_id IS NULL` to close that gap; both are declared here, so SQLite
`create_all()` enforces them too.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID

from app.infrastructure.database.models.base import Base


class CompanyMemberGrantModel(Base):
    """ORM model for company_member_grants (D8 per-user grant/deny rows)."""

    __tablename__ = "company_member_grants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id = Column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    permission = Column(String(64), nullable=False)
    effect = Column(String(8), nullable=False)
    # NULL = company-wide; set = scoped to one project of the company.
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    granted_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    granted_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint("effect IN ('grant','deny')", name="ck_company_member_grants_effect"),
        UniqueConstraint(
            "company_id",
            "user_id",
            "permission",
            "project_id",
            name="uq_company_member_grants_scope",
        ),
        Index("ix_company_member_grants_user_company", "user_id", "company_id"),
        # At most one company-wide (project_id IS NULL) grant/deny per (company_id,
        # user_id, permission): the scope constraint above treats NULLs as distinct.
        # Same partial index as migration 2ca24be9e3a8, on SQLite too.
        Index(
            "ix_company_member_grants_company_wide_unique",
            "company_id",
            "user_id",
            "permission",
            unique=True,
            postgresql_where=text("project_id IS NULL"),
            sqlite_where=text("project_id IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<CompanyMemberGrant company={self.company_id} user={self.user_id} {self.permission}={self.effect}>"
