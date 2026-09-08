"""Project assignment use cases — assign a company member to a project.

An assignment is a row in `user_projects`. It carries no role: what the
assignee may do on the project comes from their company role
(`admin` | `manager` | `member`) plus their per-member grant/deny rows, so
assigning is a pure "this person works on this project" statement.

Authorization is resolved through the resolver
(`app.application.authz.ports.AuthzReaderPort`) with an explicit company_id:

  - admin of the project's company: may assign/unassign ANY company member.
  - manager assigned to the project: may assign/unassign company `member`s
    only (never another manager or an admin).
  - anyone else: 403.
  - the person must already be a member of the project's company (404
    otherwise — a stranger cannot be assigned, that is the invitation's job).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from app.domain.entities.project_membership import ProjectMembership

if TYPE_CHECKING:
    from app.application.authz.ports import AuthzReaderPort
    from app.application.companies.ports import UserCompanyAccessRepositoryPort
    from app.application.invitations.ports import ProjectMembershipRepositoryPort


class AssignmentError(Exception):
    """Base class for project-assignment errors."""


class ProjectCompanyUnresolvedError(AssignmentError):
    """The project does not exist or has no owning company (orphaned FK)."""


class AssignmentForbiddenError(AssignmentError):
    """Caller is neither a company admin nor a manager assigned to this project
    (or a manager acted on someone who is not a plain company member)."""


class TargetNotCompanyMemberError(AssignmentError):
    """The named user has no `user_company_access` row for this project's company."""


@dataclass(frozen=True)
class AssignProjectMemberInput:
    caller_id: UUID
    project_id: UUID
    target_user_id: UUID


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


def _forbid_manager_on_a_non_member(
    authz_reader: "AuthzReaderPort",
    caller_role: str,
    user_id: UUID,
    company_id: UUID,
) -> None:
    """A manager may only act on people whose company role is `member`."""
    if caller_role != "manager":
        return
    if authz_reader.company_role_for(user_id, company_id) != "member":
        raise AssignmentForbiddenError("A manager may only assign or unassign a company 'member'")


class AssignProjectMemberUseCase:
    """PUT /projects/<id>/assignments/<user_id> — assign a company member to a project."""

    def __init__(
        self,
        authz_reader: "AuthzReaderPort",
        access_repo: "UserCompanyAccessRepositoryPort",
        membership_repo: "ProjectMembershipRepositoryPort",
    ) -> None:
        self._authz = authz_reader
        self._access = access_repo
        self._membership = membership_repo

    def execute(self, inp: AssignProjectMemberInput) -> None:
        company_id, caller_role = _resolve_caller_scope(self._authz, inp.caller_id, inp.project_id)

        if self._access.find(inp.target_user_id, company_id) is None:
            raise TargetNotCompanyMemberError(f"user {inp.target_user_id} is not a member of company {company_id}")

        _forbid_manager_on_a_non_member(self._authz, caller_role, inp.target_user_id, company_id)

        # Idempotent: `add` is an INSERT ... ON CONFLICT DO NOTHING.
        self._membership.add(
            ProjectMembership.create(
                user_id=inp.target_user_id,
                project_id=inp.project_id,
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
    ) -> None:
        self._authz = authz_reader
        self._access = access_repo
        self._membership = membership_repo

    def execute(self, caller_id: UUID, project_id: UUID, target_user_id: UUID) -> None:
        company_id, caller_role = _resolve_caller_scope(self._authz, caller_id, project_id)

        _forbid_manager_on_a_non_member(self._authz, caller_role, target_user_id, company_id)

        if self._access.find(target_user_id, company_id) is None:
            raise TargetNotCompanyMemberError(f"user {target_user_id} is not a member of company {company_id}")

        self._membership.remove(target_user_id, project_id)
