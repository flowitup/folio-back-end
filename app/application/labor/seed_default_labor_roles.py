"""SeedDefaultLaborRolesUseCase — create the two default labor roles for a new company.

Used by company creation (Phase 2 onboarding slice, `CreateCompanyUseCase`)
so every new company starts with the two roles the original global seed
migration (`f2a3b4c5d6e7_add_labor_roles.py`) shipped — same colors and slugs,
scoped to the new company and named in its creator's language ("Ouvrier
principal" / "Ouvrier auxiliaire" in French, "Thợ chính" / "Thợ phụ" in
Vietnamese, the default). Clients translate them by slug for every viewer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from app.application.labor.default_role_names import default_roles_for
from app.application.labor.labor_role_ports import ILaborRoleRepository
from app.domain.entities.labor_role import LaborRole

# The Vietnamese roster: mirrors migrations/versions/f2a3b4c5d6e7_add_labor_roles.py
# (name, color) plus the slug backfilled onto those rows by
# app.infrastructure.database.backfills.labor_roles_company.
DEFAULT_ROLES: tuple[tuple[str, str, str], ...] = tuple(default_roles_for("vi"))


class SeedDefaultLaborRolesUseCase:
    """Create the default labor role roster for a newly created company.

    Idempotent per company: skips any (name, company_id) pair that already
    exists (a role a company already has, e.g. re-run after a partial
    failure) rather than raising a duplicate-name conflict.
    """

    def __init__(self, repo: ILaborRoleRepository, db_session: object) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, company_id: UUID, locale: Optional[str] = None) -> list[LaborRole]:
        """Create the default roles for `company_id`, named in `locale` (fr/en/vi, else Vietnamese).

        Returns the created (or pre-existing) roles.
        """
        created: list[LaborRole] = []
        now = datetime.now(timezone.utc)
        for name, color, slug in default_roles_for(locale):
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
