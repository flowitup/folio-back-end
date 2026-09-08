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

    def project_ids_for_company(self, company_id: UUID) -> list[UUID]:
        """Return every project id owned by `company_id`.

        Used by the company directory (``GET /companies/<id>/persons``) to
        resolve, per member, which of THIS company's projects they are
        assigned to (never another company's, even if the same user happens
        to also work for it).
        """
        ...

    def assigned_project_ids(self, user_id: UUID, project_ids: list[UUID]) -> list[UUID]:
        """Return the subset of `project_ids` the user has a `user_projects` row for."""
        ...

    def assigned_project_ids_for_users(self, company_id: UUID, user_ids: list[UUID]) -> "dict[UUID, list[UUID]]":
        """Batch form of `assigned_project_ids` scoped to one company (H5).

        Returns `{user_id: [project_id, ...]}` for every user in `user_ids`
        that has at least one `user_projects` row on one of `company_id`'s
        projects — a user with none is simply absent from the returned dict
        (callers should default to `[]`). A single `IN`-based query, used by
        the company directory (`GET /companies/<id>/persons`) so listing N
        members costs one query instead of N.
        """
        ...

    def has_project_assignment_in_company(self, user_id: UUID, company_id: UUID) -> bool:
        """Return True if the user has a ``user_projects`` row on ANY project of `company_id`.

        Used by the company_events notification feed (Phase 2 onboarding
        slice): a member attached to a company but never assigned to one of
        its projects is a dead end an admin should notice.
        """
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
