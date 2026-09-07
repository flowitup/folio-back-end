"""Authorization domain service."""

from typing import Callable, List, Optional, Set, TYPE_CHECKING
from uuid import UUID

from app.application.ports.user_repository import UserRepositoryPort
from app.domain.companies.roles import CompanyRole

if TYPE_CHECKING:
    from app.domain.entities.user import User

# Signature: (user_id, company_id) -> role string ("admin" | "member" | ...) or
# None when the user has no access row for that company. Backed by
# UserCompanyAccessRepositoryPort.find in production (wired post-construction
# in app/__init__.py, since this service is built before the companies
# repositories exist); left None in call sites that only exercise legacy RBAC.
CompanyRoleLookup = Callable[[UUID, UUID], Optional[str]]


class AuthorizationService:
    """Domain service for authorization/RBAC logic."""

    def __init__(
        self,
        user_repository: UserRepositoryPort,
        company_role_lookup: Optional[CompanyRoleLookup] = None,
    ):
        self._user_repo = user_repository
        self._company_role_lookup = company_role_lookup

    def set_company_role_lookup(self, lookup: CompanyRoleLookup) -> None:
        """Inject the per-company role lookup after construction.

        AuthorizationService is built in wiring.py before the companies
        UserCompanyAccessRepositoryPort adapter exists, so app/__init__.py
        calls this once that adapter is available.
        """
        self._company_role_lookup = lookup

    def _get_user(self, user_id: UUID) -> Optional["User"]:
        """Get user by ID (single lookup point)."""
        return self._user_repo.find_by_id(user_id)

    def get_user_permissions(self, user_id: UUID) -> Set[str]:
        """Get all permissions for user (aggregated from roles)."""
        user = self._get_user(user_id)
        if not user:
            return set()

        permissions: Set[str] = set()
        for role in user.roles:
            for perm in role.permissions:
                permissions.add(perm.name)
        return permissions

    def _check_permission(self, permission: str, user_perms: Set[str]) -> bool:
        """Check if permission exists in user's permission set."""
        if permission in user_perms:
            return True
        if "*:*" in user_perms:
            return True
        resource = permission.split(":")[0] if ":" in permission else permission
        if f"{resource}:*" in user_perms:
            return True
        return False

    def has_permission(self, user_id: UUID, permission: str) -> bool:
        """Check if user has specific permission."""
        user_perms = self.get_user_permissions(user_id)
        return self._check_permission(permission, user_perms)

    def has_any_permission(self, user_id: UUID, permissions: List[str]) -> bool:
        """Check if user has any of the permissions."""
        user_perms = self.get_user_permissions(user_id)
        return any(self._check_permission(p, user_perms) for p in permissions)

    def has_all_permissions(self, user_id: UUID, permissions: List[str]) -> bool:
        """Check if user has all permissions."""
        user_perms = self.get_user_permissions(user_id)
        return all(self._check_permission(p, user_perms) for p in permissions)

    def has_role(self, user_id: UUID, role_name: str) -> bool:
        """Check if user has specific role."""
        user = self._get_user(user_id)
        if not user:
            return False
        return any(r.name == role_name.lower() for r in user.roles)

    def is_platform_admin(self, user_id: UUID) -> bool:
        """Return True if user_id holds the legacy global '*:*' wildcard permission.

        This is the platform-ops bypass: it grants access to every company
        regardless of any per-company role.
        """
        return self.has_permission(user_id, "*:*")

    def is_company_admin(self, user_id: UUID, company_id: UUID) -> bool:
        """Return True if user_id's per-company role for company_id is 'admin'.

        Backed by the injected company-role lookup (see set_company_role_lookup).
        Returns False when no lookup was injected (legacy-only call sites, e.g.
        some unit tests) or when the user has no access row for this company.
        Does NOT fall back to is_platform_admin — callers combine both checks
        explicitly where a platform-admin bypass is desired.
        """
        if self._company_role_lookup is None:
            return False
        role = self._company_role_lookup(user_id, company_id)
        return role == CompanyRole.ADMIN.value
