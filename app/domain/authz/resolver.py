"""Company/project-aware permission resolver.

``effective_permissions`` is the one function every authorization check in
the codebase should eventually route through (Phase 3 makes it authoritative;
Phase 1 plugs it in additively alongside the legacy JWT-claim union — see
``app.api.v1.projects.decorators``).

    effective = (matrix[role] ∪ grants) − denies

``grants``/``denies`` come from ``AuthzReaderPort.grants_for`` (D8 per-user
customisation), stubbed to ``[]`` until Phase 2. ``project:read`` is never
removed by a deny, even if the reader returns one (defense in depth — the
D8 write path is also expected to reject a ``project:read`` deny outright).

Pure Python aside from the ``AuthzReaderPort`` it is handed — no Flask,
no direct SQLAlchemy import. The Flask-request memoization layer lives in
``app.api.v1.authz_context``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.domain.authz.matrix import NON_DENIABLE, permissions_for

if TYPE_CHECKING:
    from app.application.authz.ports import AuthzReaderPort

# Permissions namespaced "company:*" only make sense evaluated against one
# specific company — there is no cross-company meaning for "manage_billing".
_COMPANY_NAMESPACE = "company"


def requires_company(permission: str) -> bool:
    """Return True when `permission` can only be evaluated against a resolved company.

    Used to guard callers of `has_permission` that pass a `company:*` permission
    without a `project_id`/`company_id` the resolver can resolve to a real company.
    """
    resource = permission.split(":", 1)[0]
    return resource == _COMPANY_NAMESPACE


def _resolve_company_id(
    reader: "AuthzReaderPort", project_id: "UUID | None", company_id: "UUID | None"
) -> "UUID | None":
    """Resolve the company to evaluate against: project_id wins when given."""
    if project_id is not None:
        return reader.project_company_id(project_id)
    return company_id


def effective_permissions(
    reader: "AuthzReaderPort",
    user_id: UUID,
    *,
    project_id: "UUID | None" = None,
    company_id: "UUID | None" = None,
    is_platform_admin: bool = False,
) -> "frozenset[str]":
    """Return the caller's effective permission set for a project or company.

    Resolution order:
      1. `is_platform_admin=True` short-circuits to `{"*:*"}` (legacy global
         admin / ops flag — never derived from the matrix itself).
      2. `project_id` given → the company is derived from the project
         (`AuthzReaderPort.project_company_id`); an unresolvable project
         (deleted, orphaned FK, or simply missing) yields an empty set rather
         than raising, since callers use this for read/mutate gating on a
         URL-supplied id that may not exist.
      3. Only `company_id` given → used as-is.
      4. Neither given (non-project, non-company routes, e.g. a future
         `/auth/me`) → `project:create` + the universal `user:read` for every
         admin of at least one company. Never `company:*` — that permission
         only has meaning evaluated against one resolved company (see
         `requires_company`), so it would be meaningless (and dangerous, if a
         caller ever forgot to re-check `has_permission` against a real
         company) to hand out here.
    Manager/member additionally require `AuthzReaderPort.is_assigned` on the
    resolved `project_id`; without a `project_id` they only get the
    always-on, non-project permissions (see `matrix.permissions_for`).
    """
    if is_platform_admin:
        return frozenset({"*:*"})

    if project_id is None and company_id is None:
        perms = {"user:read"}
        if reader.admin_company_ids(user_id):
            perms |= {"project:create"}
        return frozenset(perms)

    resolved_company_id = _resolve_company_id(reader, project_id, company_id)
    if resolved_company_id is None:
        # A project_id was given but its owning company could not be resolved.
        return frozenset()

    role = reader.company_role_for(user_id, resolved_company_id)
    if role is None:
        return frozenset()

    assigned = True
    if role != "admin":
        assigned = reader.is_assigned(user_id, project_id) if project_id is not None else False

    base = permissions_for(role, assigned)
    grant_rows = reader.grants_for(user_id, resolved_company_id, project_id)
    grants = {perm for perm, effect in grant_rows if effect == "grant"}
    denies = {perm for perm, effect in grant_rows if effect == "deny"} - NON_DENIABLE
    return frozenset((base | grants) - denies)


def denied_permissions(
    reader: "AuthzReaderPort",
    user_id: UUID,
    *,
    project_id: "UUID | None" = None,
    company_id: "UUID | None" = None,
    is_platform_admin: bool = False,
) -> "frozenset[str]":
    """Return the caller's explicit D8 deny rows for this project/company scope.

    Callers that maintain their OWN permission union outside this module
    (e.g. `app.api.v1.projects.decorators._effective_permissions`, which
    unions a legacy JWT-claim permission set with the resolver's output) must
    subtract this result from that union — otherwise an admin-managed deny
    row can never override a permission a legacy global role happens to also
    grant, which defeats the point of D8 (deny always wins).

    `effective_permissions` already applies deny rows to its OWN grant/base
    union internally; this function exists only for a caller that needs the
    deny set in isolation, and mirrors that logic exactly (never removes
    `NON_DENIABLE` permissions, always empty for a platform `*:*` holder).

    Returns an empty set whenever there is nothing to deny against: platform
    admin, no resolvable company, or no company role for the caller there.
    """
    if is_platform_admin:
        return frozenset()

    resolved_company_id = _resolve_company_id(reader, project_id, company_id)
    if resolved_company_id is None:
        return frozenset()

    role = reader.company_role_for(user_id, resolved_company_id)
    if role is None:
        return frozenset()

    grant_rows = reader.grants_for(user_id, resolved_company_id, project_id)
    return frozenset({perm for perm, effect in grant_rows if effect == "deny"} - NON_DENIABLE)


def has_permission(
    reader: "AuthzReaderPort",
    user_id: UUID,
    permission: str,
    *,
    project_id: "UUID | None" = None,
    company_id: "UUID | None" = None,
    is_platform_admin: bool = False,
) -> bool:
    """Return whether the caller's effective permissions include `permission`.

    Supports the `"*:*"` and `"<resource>:*"` wildcards the rest of the codebase
    already relies on (see `app.api.v1.projects.decorators._has_permission`).

    Raises:
        ValueError: `permission` is a `company:*` permission but neither
            `project_id` nor `company_id` resolves to an actual company —
            evaluating a company-scoped permission with no company is a
            caller bug, not a "no access" result.
    """
    if is_platform_admin:
        return True

    if requires_company(permission) and _resolve_company_id(reader, project_id, company_id) is None:
        raise ValueError(f"permission {permission!r} requires a resolvable company (got none)")

    perms = effective_permissions(
        reader,
        user_id,
        project_id=project_id,
        company_id=company_id,
        is_platform_admin=is_platform_admin,
    )
    if "*:*" in perms or permission in perms:
        return True
    resource = permission.split(":", 1)[0]
    return f"{resource}:*" in perms
