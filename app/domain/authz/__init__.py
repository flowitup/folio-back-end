"""Company-tenant authorization domain: permission matrix + resolver.

Public surface re-exported for convenience:
    - ``matrix.permissions_for(role, assigned)`` — pure matrix lookup.
    - ``matrix.CUSTOMISABLE_PERMISSIONS`` / ``matrix.NON_DENIABLE`` — D8 whitelist.
    - ``resolver.effective_permissions(...)`` — company/project-aware resolution.
"""

from app.domain.authz.matrix import (
    CUSTOMISABLE_PERMISSIONS,
    NON_DENIABLE,
    permissions_for,
)
from app.domain.authz.resolver import effective_permissions, has_permission, requires_company

__all__ = [
    "CUSTOMISABLE_PERMISSIONS",
    "NON_DENIABLE",
    "permissions_for",
    "effective_permissions",
    "has_permission",
    "requires_company",
]
