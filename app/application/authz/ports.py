"""Ports (interfaces) for the authz application layer.

``AuthzReaderPort`` is the single read surface the domain resolver
(``app.domain.authz.resolver``) needs: company role, project assignment,
project→company lookup, the caller's admin/primary companies, and D8
grant/deny rows read from ``company_member_grants`` (Phase 2).
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class AuthzReaderPort(Protocol):
    """Read-only queries the authz resolver needs to compute effective permissions."""

    def company_role_for(self, user_id: UUID, company_id: UUID) -> "str | None":
        """Return the caller's company role ("admin" | "manager" | "member"), or None
        if the user has no ``user_company_access`` row for that company."""
        ...

    def is_assigned(self, user_id: UUID, project_id: UUID) -> bool:
        """Return True if the user has a ``user_projects`` row for this project.

        Manager/member permissions on a specific project require this to be
        True; admin does not (implicit on every project of their company).
        """
        ...

    def project_company_id(self, project_id: UUID) -> "UUID | None":
        """Return the ``company_id`` owning a project, or None if the project
        does not exist or has no company (orphaned FK)."""
        ...

    def primary_company_id(self, user_id: UUID) -> "UUID | None":
        """Return the company_id of the user's ``is_primary=True`` access row,
        or None if the user has no primary company."""
        ...

    def admin_company_ids(self, user_id: UUID) -> list[UUID]:
        """Return every company_id where the user holds the "admin" role."""
        ...

    def grants_for(self, user_id: UUID, company_id: UUID, project_id: "UUID | None") -> list[tuple[str, str]]:
        """Return the caller's explicit D8 grant/deny rows as (permission, effect) pairs.

        ``effect`` is "grant" or "deny". Rows are scoped to `company_id`
        and either company-wide (``project_id IS NULL`` on the row) or
        matching the given `project_id` exactly — a row scoped to a
        different project never applies. Returns ``[]`` when there is
        nothing to apply (never raises), so the resolver degrades to
        matrix-only permissions.
        """
        ...
