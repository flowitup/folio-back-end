"""SeedDefaultLaborRolesUseCase — create the two default labor roles for a new company.

Used by company creation (Phase 2 onboarding slice, `CreateCompanyUseCase`)
so every new company starts with the same "Thợ chính" / "Thợ phụ" roster the
original global seed migration (`f2a3b4c5d6e7_add_labor_roles.py`) shipped —
same names, colors, and slugs, just scoped to the new company instead of
shared globally.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.application.labor.labor_role_ports import ILaborRoleRepository
from app.domain.entities.labor_role import LaborRole

# Mirrors migrations/versions/f2a3b4c5d6e7_add_labor_roles.py exactly (name, color)
# plus the slug backfilled onto those same rows by
# app.infrastructure.database.backfills.labor_roles_company.
DEFAULT_ROLES: tuple[tuple[str, str, str], ...] = (
    ("Thợ chính", "#3B82F6", "tho_chinh"),
    ("Thợ phụ", "#10B981", "tho_phu"),
)


class SeedDefaultLaborRolesUseCase:
    """Create the default labor role roster for a newly created company.

    Idempotent per company: skips any (name, company_id) pair that already
    exists (a role a company already has, e.g. re-run after a partial
    failure) rather than raising a duplicate-name conflict.
    """

    def __init__(self, repo: ILaborRoleRepository, db_session: object) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, company_id: UUID) -> list[LaborRole]:
        """Create the default roles for `company_id`. Returns the created (or pre-existing) roles."""
        created: list[LaborRole] = []
        now = datetime.now(timezone.utc)
        for name, color, slug in DEFAULT_ROLES:
            existing = self._repo.find_by_name(name, company_id=company_id)
            if existing is not None:
                created.append(existing)
                continue
            role = LaborRole(
                id=uuid4(),
                name=name,
                color=color,
                created_at=now,
                company_id=company_id,
                slug=slug,
            )
            created.append(self._repo.create(role))
        self._db.commit()
        return created
