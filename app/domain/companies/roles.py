"""Per-company role for the companies bounded context.

A user's role within a single company (stored on user_company_access.role and
carried by invite tokens). Distinct from the global RBAC permissions in the JWT:
this governs company-scoped capabilities such as access to the company's billing.
"""

from __future__ import annotations

from enum import Enum


class CompanyRole(str, Enum):
    """Role a user holds within a specific company.

    admin   → full company management (billing, members, settings) and
              implicit admin/read-write on every project of the company.
    manager → assigned per-project; full labor/invoice/document read-write on
              projects they are assigned to, no company management, no
              project create/delete. See app.domain.authz.matrix for the
              exact permission set derived from this role.
    member  → assigned per-project; read-only on assigned projects plus
              logging their own attendance.
    """

    ADMIN = "admin"
    MANAGER = "manager"
    MEMBER = "member"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        """Return all valid role string values (for CHECK constraints / validation)."""
        return tuple(r.value for r in cls)
