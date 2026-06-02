"""Per-company role for the companies bounded context.

A user's role within a single company (stored on user_company_access.role and
carried by invite tokens). Distinct from the global RBAC permissions in the JWT:
this governs company-scoped capabilities such as access to the company's billing.
"""

from __future__ import annotations

from enum import Enum


class CompanyRole(str, Enum):
    """Role a user holds within a specific company.

    admin  → may view and manage the company's billing + members.
    member → attached to the company but without billing/member-admin access.
    """

    ADMIN = "admin"
    MEMBER = "member"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        """Return all valid role string values (for CHECK constraints / validation)."""
        return tuple(r.value for r in cls)
