"""ManageGrantsUseCase — admin-managed D8 per-member permission grant/deny rows.

D8: an admin may customise a manager's or member's effective permission set,
company-wide or scoped to one project, from a fixed whitelist
(`app.domain.authz.matrix.CUSTOMISABLE_PERMISSIONS`). Never `company:*`,
`*:*`, `project:create`/`project:delete` (admin-only capabilities, immune to
customisation), and `project:read` can never be denied
(`app.domain.authz.matrix.NON_DENIABLE`). Only manager/member targets are
customisable — an admin's permissions are never per-user scoped.

The resolver (`app.domain.authz.resolver.effective_permissions`) folds these
rows in on every request via `AuthzReaderPort.grants_for` — nothing here
touches the resolver directly; this use case only owns the CRUD + validation
surface for the underlying `company_member_grants` table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional
from uuid import UUID, uuid4

from app.application.company_persons.grants_ports import (
    CompanyExistsPort,
    GrantsRoleCheckerPort,
    MemberGrant,
    MemberGrantRepositoryPort,
    ProjectCompanyResolverPort,
    UserCompanyAccessLookupPort,
)
from app.domain.authz.matrix import CUSTOMISABLE_PERMISSIONS, NON_DENIABLE

_VALID_EFFECTS = frozenset({"grant", "deny"})
_CUSTOMISABLE_ROLES = frozenset({"manager", "member"})


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ListGrantsInput:
    caller_id: UUID
    company_id: UUID
    user_id: UUID


@dataclass(frozen=True)
class ListGrantsResult:
    grants: List[MemberGrant]
    customisable: List[str]


@dataclass(frozen=True)
class SetGrantInput:
    caller_id: UUID
    company_id: UUID
    user_id: UUID
    permission: str
    effect: str
    project_id: Optional[UUID] = None


@dataclass(frozen=True)
class RemoveGrantInput:
    caller_id: UUID
    company_id: UUID
    user_id: UUID
    permission: str
    project_id: Optional[UUID] = None


# ---------------------------------------------------------------------------
# Exceptions — the route layer maps each of these to an HTTP status code.
# ---------------------------------------------------------------------------


class GrantsError(Exception):
    """Base class for every ManageGrantsUseCase error."""


class ForbiddenGrantsCallerError(GrantsError):
    """Caller is neither a platform admin nor a company admin of `company_id` (→ 403)."""

    def __init__(self, caller_id: UUID, company_id: UUID) -> None:
        super().__init__(f"User {caller_id} is not an admin of company {company_id}")
        self.caller_id = caller_id
        self.company_id = company_id


class CompanyNotFoundForGrantsError(GrantsError):
    """`company_id` does not exist (→ 404)."""

    def __init__(self, company_id: UUID) -> None:
        super().__init__(f"Company {company_id} not found")
        self.company_id = company_id


class InvalidGrantPermissionError(GrantsError):
    """`permission` is outside `CUSTOMISABLE_PERMISSIONS` (→ 400)."""

    def __init__(self, permission: str) -> None:
        super().__init__(
            f"Permission {permission!r} is not customisable " f"(expected one of {sorted(CUSTOMISABLE_PERMISSIONS)})"
        )
        self.permission = permission


class InvalidGrantEffectError(GrantsError):
    """`effect` is neither 'grant' nor 'deny' (→ 400)."""

    def __init__(self, effect: str) -> None:
        super().__init__(f"Invalid effect {effect!r} (expected 'grant' or 'deny')")
        self.effect = effect


class NonDeniablePermissionError(GrantsError):
    """Attempted to deny a `NON_DENIABLE` permission, e.g. `project:read` (→ 400)."""

    def __init__(self, permission: str) -> None:
        super().__init__(f"Permission {permission!r} can never be denied")
        self.permission = permission


class TargetNotCompanyMemberError(GrantsError):
    """`user_id` has no role in `company_id` (→ 404)."""

    def __init__(self, company_id: UUID, user_id: UUID) -> None:
        super().__init__(f"User {user_id} is not attached to company {company_id}")
        self.company_id = company_id
        self.user_id = user_id


class TargetNotCustomisableError(GrantsError):
    """`user_id` holds a role D8 does not allow customising, e.g. admin (→ 400)."""

    def __init__(self, company_id: UUID, user_id: UUID, role: str) -> None:
        super().__init__(f"User {user_id}'s role {role!r} in company {company_id} is not customisable")
        self.company_id = company_id
        self.user_id = user_id
        self.role = role


class ProjectNotInCompanyError(GrantsError):
    """`project_id` does not exist, or does not belong to `company_id` (→ 404)."""

    def __init__(self, company_id: UUID, project_id: UUID) -> None:
        super().__init__(f"Project {project_id} does not belong to company {company_id}")
        self.company_id = company_id
        self.project_id = project_id


class ManageGrantsUseCase:
    """List, set (upsert), and remove D8 per-member grant/deny rows.

    Every entry point re-checks the caller is a company (or platform) admin —
    defense in depth alongside the route's `@require_company_role("admin")`
    decorator, so a future caller that bypasses the decorator (or a direct
    use-case unit test) cannot skip the guard.
    """

    def __init__(
        self,
        grant_repo: MemberGrantRepositoryPort,
        access_repo: UserCompanyAccessLookupPort,
        company_repo: CompanyExistsPort,
        project_resolver: ProjectCompanyResolverPort,
        role_checker: GrantsRoleCheckerPort,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._grant_repo = grant_repo
        self._access_repo = access_repo
        self._company_repo = company_repo
        self._project_resolver = project_resolver
        self._role_checker = role_checker
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- guards -------------------------------------------------------------

    def _assert_company_exists(self, company_id: UUID) -> None:
        if self._company_repo.find_by_id(company_id) is None:
            raise CompanyNotFoundForGrantsError(company_id)

    def _assert_caller_is_admin(self, caller_id: UUID, company_id: UUID) -> None:
        if self._role_checker.is_platform_admin(caller_id):
            return
        if self._role_checker.is_company_admin(caller_id, company_id):
            return
        raise ForbiddenGrantsCallerError(caller_id, company_id)

    def _assert_target_customisable(self, company_id: UUID, user_id: UUID) -> None:
        access = self._access_repo.find(user_id, company_id)
        if access is None:
            raise TargetNotCompanyMemberError(company_id, user_id)
        if access.role not in _CUSTOMISABLE_ROLES:
            raise TargetNotCustomisableError(company_id, user_id, access.role)

    def _assert_project_in_company(self, company_id: UUID, project_id: Optional[UUID]) -> None:
        if project_id is None:
            return
        project_company_id = self._project_resolver.company_id_for_project(project_id)
        if project_company_id is None or project_company_id != company_id:
            raise ProjectNotInCompanyError(company_id, project_id)

    # -- entry points ---------------------------------------------------------

    def list_grants(self, inp: ListGrantsInput) -> ListGrantsResult:
        """Return every grant/deny row for a member, plus the customisable whitelist."""
        self._assert_company_exists(inp.company_id)
        self._assert_caller_is_admin(inp.caller_id, inp.company_id)
        self._assert_target_customisable(inp.company_id, inp.user_id)
        grants = self._grant_repo.list_for_member(inp.company_id, inp.user_id)
        return ListGrantsResult(grants=grants, customisable=sorted(CUSTOMISABLE_PERMISSIONS))

    def set_grant(self, inp: SetGrantInput) -> MemberGrant:
        """Grant or deny one permission to a manager/member (idempotent upsert)."""
        self._assert_company_exists(inp.company_id)
        self._assert_caller_is_admin(inp.caller_id, inp.company_id)
        if inp.effect not in _VALID_EFFECTS:
            raise InvalidGrantEffectError(inp.effect)
        if inp.permission not in CUSTOMISABLE_PERMISSIONS:
            raise InvalidGrantPermissionError(inp.permission)
        if inp.effect == "deny" and inp.permission in NON_DENIABLE:
            raise NonDeniablePermissionError(inp.permission)
        self._assert_target_customisable(inp.company_id, inp.user_id)
        self._assert_project_in_company(inp.company_id, inp.project_id)

        grant = MemberGrant(
            id=uuid4(),
            company_id=inp.company_id,
            user_id=inp.user_id,
            permission=inp.permission,
            effect=inp.effect,
            project_id=inp.project_id,
            granted_by_user_id=inp.caller_id,
            granted_at=self._clock(),
        )
        return self._grant_repo.upsert(grant)

    def remove_grant(self, inp: RemoveGrantInput) -> bool:
        """Delete a grant/deny row. Returns True if a row was deleted, False if none matched."""
        self._assert_company_exists(inp.company_id)
        self._assert_caller_is_admin(inp.caller_id, inp.company_id)
        if inp.permission not in CUSTOMISABLE_PERMISSIONS:
            raise InvalidGrantPermissionError(inp.permission)
        self._assert_target_customisable(inp.company_id, inp.user_id)
        self._assert_project_in_company(inp.company_id, inp.project_id)
        return self._grant_repo.delete(inp.company_id, inp.user_id, inp.permission, inp.project_id)
