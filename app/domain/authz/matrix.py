"""Permission matrix — the single source of truth for company-role permissions.

Company is the tenant: every user holds exactly one role per company
(``admin`` | ``manager`` | ``member``, see ``app.domain.companies.roles.CompanyRole``).
Permissions are derived here, in code — never stored on the role itself. The
only per-user exception is an explicit admin-managed grant/deny row (D8,
``CUSTOMISABLE_PERMISSIONS``), applied on top of this matrix by the resolver
(``app.domain.authz.resolver``).

Pure Python — no Flask, no SQLAlchemy, no I/O. Safe to unit-test exhaustively
and to import from any layer without pulling in infrastructure.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Permission sets per company role.
# ---------------------------------------------------------------------------
# `admin` is granted every capability below regardless of project assignment —
# admin is implicit on every project the admin's company owns.
ADMIN_PERMISSIONS: frozenset[str] = frozenset(
    {
        # Admin-only capabilities — never granted to manager/member, never
        # customisable via D8 grant/deny rows.
        "project:create",
        "project:delete",
        "company:manage_members",
        "company:manage_settings",
        "company:manage_billing",
        # Shared with manager on an assigned project — admin gets them
        # company-wide (every project of their company).
        "project:read",
        "project:update",
        "project:invite",
        "project:manage_users",
        "project:manage_labor",
        "project:manage_invoices",
        "bibliotheque:manage",
        "project:log_own_attendance",
        "project:view_pay",
        "project:view_roster",
        "user:read",
    }
)

# `manager` holds these on a project they are assigned to (full read/write
# labor + invoices + documents, but never create/delete a project or touch
# company:* settings).
MANAGER_PROJECT_PERMISSIONS: frozenset[str] = frozenset(
    {
        "project:read",
        "project:update",
        "project:invite",
        "project:manage_users",
        "project:manage_labor",
        "project:manage_invoices",
        "bibliotheque:manage",
        "project:log_own_attendance",
        "project:view_pay",
        "project:view_roster",
    }
)

# `member` holds read-only access to the same project-scoped surface, plus
# their own attendance logging and the day roster (D3: name, presence, hours,
# day type — never rate/cost). No project:view_pay (never sees other
# workers' rate/cost) and no manage_* write permission.
MEMBER_PROJECT_PERMISSIONS: frozenset[str] = frozenset(
    {
        "project:read",
        "project:log_own_attendance",
        "project:view_roster",
    }
)

# Granted to every company role regardless of project assignment — reading
# another user's public identity (name) is not project-scoped.
UNASSIGNED_PERMISSIONS: frozenset[str] = frozenset({"user:read"})

# ---------------------------------------------------------------------------
# D8 customisation whitelist.
# ---------------------------------------------------------------------------
# Permissions an admin may grant or deny to a manager/member, company-wide or
# per project. Never company:*, *:*, project:create, or project:delete —
# those stay admin-only capabilities, immune to per-user customisation.
CUSTOMISABLE_PERMISSIONS: frozenset[str] = frozenset(
    {
        "project:update",
        "project:invite",
        "project:manage_users",
        "project:manage_labor",
        "project:manage_invoices",
        "project:log_own_attendance",
        "bibliotheque:manage",
        "project:view_pay",
    }
)

# project:read can never be denied — every assigned/company-scoped user must
# always retain at least read access.
NON_DENIABLE: frozenset[str] = frozenset({"project:read"})


def permissions_for(role: str, assigned: bool) -> frozenset[str]:
    """Return the base matrix permission set for a company role.

    Args:
        role: one of "admin", "manager", "member" (any other value yields no
            permissions — the resolver is expected to have already validated
            the caller actually holds a role in the target company).
        assigned: whether the caller is assigned to the project in question
            (a row in ``user_projects``). Ignored for "admin" — admin is
            implicit on every project of their company. For "manager" and
            "member", the project-scoped permissions above only apply when
            assigned; otherwise only the always-on, non-project permissions
            (``UNASSIGNED_PERMISSIONS``) are returned.

    Returns:
        A frozenset of permission name strings. Never raises.
    """
    if role == "admin":
        return ADMIN_PERMISSIONS
    # UNASSIGNED_PERMISSIONS are always-on: they are what a role holds *without*
    # a project, so an assignment adds to them rather than replacing them.
    if role == "manager":
        return (MANAGER_PROJECT_PERMISSIONS | UNASSIGNED_PERMISSIONS) if assigned else UNASSIGNED_PERMISSIONS
    if role == "member":
        return (MEMBER_PROJECT_PERMISSIONS | UNASSIGNED_PERMISSIONS) if assigned else UNASSIGNED_PERMISSIONS
    return frozenset()
