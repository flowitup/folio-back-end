"""Authorization domain service — a thin facade over the company-aware resolver.

Every use-case that asks "may this user do X?" through `RoleCheckerPort`
(companies, payment methods, company_persons) or `ICompanyPermissionChecker`
(bibliotheque) goes through this class, so they all resolve permissions the
same way route decorators do:

    role in the company (+ project assignment) → matrix → D8 grants/denies

Legacy global roles (`users.roles` → `role_permissions`) are NOT consulted any
more; the only bypass is the `users.is_platform_ops` flag, read through the
same `AuthzReaderPort` as the rest of the resolver.

`has_permission` here answers a *context-free* question ("may this user manage
a library at all?"), so it is true when ANY of the caller's companies grants
the permission. Project-scoped gating stays in
`app.api.v1.projects.decorators`, which resolves the concrete project.
"""

from typing import Callable, List, Optional, Set, TYPE_CHECKING
from uuid import UUID

from app.application.ports.user_repository import UserRepositoryPort
from app.domain.authz.resolver import (
    has_permission_anywhere,
    permissions_for_user,
    permissions_in_company,
)
from app.domain.companies.roles import CompanyRole

if TYPE_CHECKING:
    from app.application.authz.ports import AuthzReaderPort
    from app.domain.entities.user import User

# Signature: (user_id, company_id) -> role string ("admin" | "manager" |
# "member") or None when the user has no access row for that company. Kept as a
# fallback for call sites wired before the authz reader exists (some unit
# tests); production injects the reader instead.
CompanyRoleLookup = Callable[[UUID, UUID], Optional[str]]


class AuthorizationService:
    """Domain service implementing RoleCheckerPort + ICompanyPermissionChecker."""

    def __init__(
        self,
        user_repository: UserRepositoryPort,
        company_role_lookup: Optional[CompanyRoleLookup] = None,
        authz_reader: "Optional[AuthzReaderPort]" = None,
    ):
        self._user_repo = user_repository
        self._company_role_lookup = company_role_lookup
        self._authz_reader = authz_reader

    def set_company_role_lookup(self, lookup: CompanyRoleLookup) -> None:
        """Inject the per-company role lookup after construction (see `is_company_admin`)."""
        self._company_role_lookup = lookup

    def set_authz_reader(self, reader: "AuthzReaderPort") -> None:
        """Inject the resolver read port after construction.

        This service is built in `wiring.py` before the SQLAlchemy session-backed
        repositories exist, so `app/__init__.py` calls this once the reader is
        available. Without a reader every check degrades to False (no legacy
        fallback) — a wiring bug must fail closed, not open.
        """
        self._authz_reader = reader

    def _get_user(self, user_id: UUID) -> Optional["User"]:
        """Get user by ID (single lookup point)."""
        return self._user_repo.find_by_id(user_id)

    # -- RoleCheckerPort / ICompanyPermissionChecker ------------------------

    def is_platform_admin(self, user_id: UUID) -> bool:
        """Return the caller's `users.is_platform_ops` flag (the support bypass)."""
        if self._authz_reader is None:
            return False
        return bool(self._authz_reader.is_platform_ops(user_id))

    def is_company_admin(self, user_id: UUID, company_id: UUID) -> bool:
        """Return True when the caller's role in `company_id` is "admin".

        Does NOT imply platform ops — callers combine both checks explicitly
        where a support bypass is desired (see `companies._helpers`).
        """
        role = None
        if self._authz_reader is not None:
            role = self._authz_reader.company_role_for(user_id, company_id)
        elif self._company_role_lookup is not None:
            role = self._company_role_lookup(user_id, company_id)
        return role == CompanyRole.ADMIN.value

    def has_permission(self, user_id: UUID, permission: str) -> bool:
        """Return True when any of the caller's companies grants `permission`."""
        if self.is_platform_admin(user_id):
            return True
        if self._authz_reader is None:
            return False
        return has_permission_anywhere(self._authz_reader, user_id, permission)

    def has_permission_in_company(self, user_id: UUID, permission: str, company_id: UUID) -> bool:
        """Return True when `company_id` grants `permission` to the caller.

        The company-scoped counterpart of `has_permission`: a role held in
        another company never answers this one. Use it for every write gated on
        a specific company (library products, imports, product images).
        """
        if self.is_platform_admin(user_id):
            return True
        if self._authz_reader is None:
            return False
        perms = permissions_in_company(self._authz_reader, user_id, company_id)
        if permission in perms or "*:*" in perms:
            return True
        return f"{permission.split(':', 1)[0]}:*" in perms

    def get_user_permissions(self, user_id: UUID) -> Set[str]:
        """Resolver permissions for the caller's primary company (union when none).

        Populates the access token's `permissions` claim at login/refresh and
        the `/auth/me` payload for clients that still read them; nothing on the
        server reads that claim any more.
        """
        if self._authz_reader is None:
            return set()
        return set(
            permissions_for_user(
                self._authz_reader,
                user_id,
                is_platform_admin=self.is_platform_admin(user_id),
            )
        )

    def has_any_permission(self, user_id: UUID, permissions: List[str]) -> bool:
        """Check if the caller holds any of `permissions` in any of their companies."""
        return any(self.has_permission(user_id, p) for p in permissions)

    def has_all_permissions(self, user_id: UUID, permissions: List[str]) -> bool:
        """Check if the caller holds all of `permissions` in any of their companies."""
        return all(self.has_permission(user_id, p) for p in permissions)

    def has_role(self, user_id: UUID, role_name: str) -> bool:
        """Return True when the caller holds `role_name` in any company.

        "ops" answers the platform-ops flag; the legacy global-role table is
        never consulted.
        """
        wanted = role_name.lower()
        if wanted == "ops":
            return self.is_platform_admin(user_id)
        if self._authz_reader is None:
            return False
        return any(role == wanted for _company_id, role in self._authz_reader.company_roles_for(user_id))
