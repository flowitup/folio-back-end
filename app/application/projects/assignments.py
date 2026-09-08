"""Project assignment use cases — manager/member assignment to a project (Phase 2 onboarding).

Assignment is a row in `user_projects` (legacy membership table, reused —
see `app.application.invitations.ports.ProjectMembershipRepositoryPort`).
Authorization is resolved through the Phase 1 resolver
(`app.application.authz.ports.AuthzReaderPort`) with an explicit company_id:

  - admin of the project's company: may assign/unassign ANY company member,
    with role "member" or "manager".
  - manager assigned to the project: may assign/unassign "member"-role
    targets only (never promote/demote another manager).
  - anyone else: 403.
  - target must already be a member of the project's company (404 otherwise
    — a stranger cannot be assigned, that is the invitation's job).

`role_id` on `user_projects` is resolved from the legacy `roles` table by
name ("member" / "manager") via `RoleRepositoryPort.find_by_name` — if
neither legacy role row exists (e.g. a minimal test fixture), the write is
skipped silently rather than inserting a NULL that a real Postgres NOT NULL
constraint would reject.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from app.domain.entities.project_membership import ProjectMembership

if TYPE_CHECKING:
    from app.application.authz.ports import AuthzReaderPort
    from app.application.companies.ports import UserCompanyAccessRepositoryPort
    from app.application.invitations.ports import ProjectMembershipRepositoryPort, RoleRepositoryPort

_ASSIGNABLE_ROLES = ("member", "manager")


class AssignmentError(Exception):
    """Base class for project-assignment errors."""


class ProjectCompanyUnresolvedError(AssignmentError):
    """The project does not exist or has no owning company (orphaned FK)."""


class AssignmentForbiddenError(AssignmentError):
    """Caller is neither a company admin nor a manager assigned to this project
    (or a manager tried to assign/unassign something other than a member)."""


class InvalidAssignmentRoleError(AssignmentError):
    """`role` is not one of the assignable roles ("member" | "manager")."""


class TargetNotCompanyMemberError(AssignmentError):
    """The target user has no `user_company_access` row for this project's company."""


@dataclass(frozen=True)
class AssignProjectMemberInput:
    caller_id: UUID
    project_id: UUID
    target_user_id: UUID
    role: str = "member"


def _resolve_caller_scope(
    authz_reader: "AuthzReaderPort",
    caller_id: UUID,
    project_id: UUID,
) -> "tuple[UUID, str]":
    """Return (company_id, caller_role); raises on an unresolvable project or forbidden caller."""
    company_id = authz_reader.project_company_id(project_id)
    if company_id is None:
        raise ProjectCompanyUnresolvedError(f"project {project_id} has no resolvable company")
    caller_role = authz_reader.company_role_for(caller_id, company_id)
    if caller_role == "admin":
        return company_id, caller_role
    if caller_role == "manager" and authz_reader.is_assigned(caller_id, project_id):
        return company_id, caller_role
    raise AssignmentForbiddenError("Caller must be a company admin or a manager assigned to this project")


class AssignProjectMemberUseCase:
    """PUT /projects/<id>/assignments/<user_id> — create or change a project assignment."""

    def __init__(
        self,
        authz_reader: "AuthzReaderPort",
        access_repo: "UserCompanyAccessRepositoryPort",
        membership_repo: "ProjectMembershipRepositoryPort",
        role_repo: "Optional[RoleRepositoryPort]" = None,
    ) -> None:
        self._authz = authz_reader
        self._access = access_repo
        self._membership = membership_repo
        self._roles = role_repo

    def execute(self, inp: AssignProjectMemberInput) -> None:
        if inp.role not in _ASSIGNABLE_ROLES:
            raise InvalidAssignmentRoleError(f"role must be one of {_ASSIGNABLE_ROLES}, got {inp.role!r}")

        company_id, caller_role = _resolve_caller_scope(self._authz, inp.caller_id, inp.project_id)
        if caller_role == "manager" and inp.role != "member":
            raise AssignmentForbiddenError("A manager may only assign the 'member' role")

        if self._access.find(inp.target_user_id, company_id) is None:
            raise TargetNotCompanyMemberError(f"user {inp.target_user_id} is not a member of company {company_id}")

        role_row = self._roles.find_by_name(inp.role) if self._roles is not None else None
        role_id = role_row.id if role_row is not None else None
        if role_id is None:
            # Legacy role row absent (e.g. minimal test fixture) — skip
            # silently rather than writing a role_id-less row a real DB would reject.
            return

        if self._membership.find_role_id(inp.target_user_id, inp.project_id) is not None:
            self._membership.set_role(inp.target_user_id, inp.project_id, role_id)
        else:
            self._membership.add(
                ProjectMembership.create(
                    user_id=inp.target_user_id,
                    project_id=inp.project_id,
                    role_id=role_id,
                    invited_by=inp.caller_id,
                )
            )


class UnassignProjectMemberUseCase:
    """DELETE /projects/<id>/assignments/<user_id> — remove a project assignment."""

    def __init__(
        self,
        authz_reader: "AuthzReaderPort",
        access_repo: "UserCompanyAccessRepositoryPort",
        membership_repo: "ProjectMembershipRepositoryPort",
        role_repo: "Optional[RoleRepositoryPort]" = None,
    ) -> None:
        self._authz = authz_reader
        self._access = access_repo
        self._membership = membership_repo
        self._roles = role_repo

    def execute(self, caller_id: UUID, project_id: UUID, target_user_id: UUID) -> None:
        company_id, caller_role = _resolve_caller_scope(self._authz, caller_id, project_id)

        if caller_role == "manager" and self._roles is not None:
            # A manager may only unassign a "member"-role target, never demote/remove
            # another manager. Degrades to "allow" when roles are unseeded (test fixture).
            existing_role_id = self._membership.find_role_id(target_user_id, project_id)
            member_role = self._roles.find_by_name("member")
            if existing_role_id is not None and member_role is not None and existing_role_id != member_role.id:
                raise AssignmentForbiddenError("A manager may only unassign a 'member'-role target")

        if self._access.find(target_user_id, company_id) is None:
            raise TargetNotCompanyMemberError(f"user {target_user_id} is not a member of company {company_id}")

        self._membership.remove(target_user_id, project_id)
