"""Authorization domain service."""

from typing import List, Optional, Set, TYPE_CHECKING
from uuid import UUID

from app.application.ports.user_repository import UserRepositoryPort

if TYPE_CHECKING:
    from app.domain.entities.user import User


class AuthorizationService:
    """Domain service for authorization/RBAC logic."""

    def __init__(self, user_repository: UserRepositoryPort):
        self._user_repo = user_repository

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
