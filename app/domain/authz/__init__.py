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
from app.domain.authz.resolver import (
    denied_permissions,
    effective_permissions,
    has_permission,
    has_permission_anywhere,
    permissions_anywhere,
    permissions_for_user,
    permissions_in_company,
    requires_company,
)

__all__ = [
    "CUSTOMISABLE_PERMISSIONS",
    "NON_DENIABLE",
    "permissions_for",
    "denied_permissions",
    "effective_permissions",
    "has_permission",
    "has_permission_anywhere",
    "permissions_anywhere",
    "permissions_for_user",
    "permissions_in_company",
    "requires_company",
]
