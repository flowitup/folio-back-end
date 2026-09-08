"""Repository + collaborator ports for D8 per-member grant/deny management.

`MemberGrantRepositoryPort` is the persistence contract for
`company_member_grants` (D8) — implemented by
`app.infrastructure.database.repositories.sqlalchemy_company_member_grant_repository
.SqlAlchemyCompanyMemberGrantRepository` and consumed by
`ManageGrantsUseCase` (`app.application.company_persons.manage_grants_usecase`).

The remaining protocols below (`UserCompanyAccessLookupPort`, `CompanyExistsPort`,
`ProjectCompanyResolverPort`, `GrantsRoleCheckerPort`) are declared here rather
than imported from `app.application.companies.ports` / `app.application.projects`
on purpose: this codebase's convention (see `app.application.payment_methods
.ports.RoleCheckerPort` duplicating `app.application.companies.ports
.RoleCheckerPort`) is for each bounded context to own structurally-typed
Protocols satisfied by the SAME concrete adapters, rather than cross-import
between bounded contexts. `ManageGrantsUseCase` is wired directly in
`app.api.v1.companies.grants_routes` from the shared DI container
(`user_company_access_repo`, `company_repo`, `authorization_service`) plus a
route-local ORM lookup for project → company_id (the domain `Project` entity
intentionally omits `company_id` — see
`app.api.v1.projects.decorators._get_project_company_id_from_orm`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Protocol
from uuid import UUID


@dataclass(frozen=True)
class MemberGrant:
    """A single D8 grant/deny row (mirrors `CompanyMemberGrantModel`)."""

    id: UUID
    company_id: UUID
    user_id: UUID
    permission: str
    effect: str  # "grant" | "deny"
    project_id: Optional[UUID]
    granted_by_user_id: Optional[UUID]
    granted_at: datetime


class MemberGrantRepositoryPort(Protocol):
    """Persistence contract for `company_member_grants` (D8) rows."""

    def list_for_member(self, company_id: UUID, user_id: UUID) -> List[MemberGrant]:
        """Return every grant/deny row for (company_id, user_id), oldest first."""
        ...

    def upsert(self, grant: MemberGrant) -> MemberGrant:
        """Insert or replace the row matching (company_id, user_id, permission, project_id).

        `project_id=None` (company-wide) and a specific project_id are distinct
        scopes — the unique key is the full 4-tuple, NULL included. Replacing an
        existing row updates `effect`/`granted_by_user_id`/`granted_at` in place
        (setting a new effect on the same key is idempotent replacement, not a
        second row).
        """
        ...

    def delete(self, company_id: UUID, user_id: UUID, permission: str, project_id: Optional[UUID]) -> bool:
        """Delete the row matching the exact (company_id, user_id, permission, project_id) key.

        Returns True if a row was deleted, False if no matching row existed.
        """
        ...


class _AccessRow(Protocol):
    role: str


class UserCompanyAccessLookupPort(Protocol):
    """Minimal read contract this use case needs from a user/company access repo."""

    def find(self, user_id: UUID, company_id: UUID) -> Optional[_AccessRow]:
        """Return the access row for (user_id, company_id), or None if unattached."""
        ...


class CompanyExistsPort(Protocol):
    """Minimal read contract this use case needs from a company repo."""

    def find_by_id(self, company_id: UUID) -> Optional[object]:
        """Return the company, or None if it does not exist."""
        ...


class ProjectCompanyResolverPort(Protocol):
    """Resolves a project's owning company_id (project_id must belong to it)."""

    def company_id_for_project(self, project_id: UUID) -> Optional[UUID]:
        """Return the `company_id` owning `project_id`, or None if missing/orphaned."""
        ...


class GrantsRoleCheckerPort(Protocol):
    """Minimal role-checking contract for the defense-in-depth admin re-check."""

    def is_platform_admin(self, user_id: UUID) -> bool:
        """Return True if user_id holds the legacy global '*:*' wildcard permission."""
        ...

    def is_company_admin(self, user_id: UUID, company_id: UUID) -> bool:
        """Return True if user_id's per-company role for company_id is 'admin'."""
        ...
