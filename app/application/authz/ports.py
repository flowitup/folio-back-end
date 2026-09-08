"""Ports (interfaces) for the authz application layer.

``AuthzReaderPort`` is the single read surface the domain resolver
(``app.domain.authz.resolver``) needs: company role, project assignment,
project→company lookup, and the caller's admin/primary companies. Grants and
denies (D8) are stubbed to always return an empty list until the
``company_member_grants`` table lands in Phase 2 — the resolver already
folds them in correctly, so no resolver change is needed when that table
arrives.
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

        ``effect`` is "grant" or "deny". Stubbed to always return ``[]`` until
        the Phase 2 ``company_member_grants`` table exists — every adapter
        implementing this port before then MUST return an empty list, never
        raise, so the resolver degrades to matrix-only permissions.
        """
        ...
