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

Assigning may also carry an optional `role`. It does NOT live on the
assignment: `role="manager"` asks to raise the target's COMPANY role to
manager (company admins only), which is a company-wide promotion. `member`
never demotes anyone. The use case returns the target's company role after
the call so the endpoint can echo it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.application.companies._helpers import ForbiddenCompanyError
from app.application.companies.dtos import SetMemberRoleInput
from app.domain.companies.roles import CompanyRole
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
    # Company-wide: "manager" promotes the target's company role (admins only).
    role: str = CompanyRole.MEMBER.value


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
        role_setter: Any = None,  # SetMemberRoleUseCase — needed for role="manager"
        db_session: Any = None,  # TransactionalSessionPort, handed to the role setter
    ) -> None:
        self._authz = authz_reader
        self._access = access_repo
        self._membership = membership_repo
        self._role_setter = role_setter
        self._db = db_session

    def execute(self, inp: AssignProjectMemberInput) -> str:
        """Assign the target and return their company role afterwards.

        Raises `AssignmentForbiddenError` when the caller may not assign (or
        may not promote), `TargetNotCompanyMemberError` when the target has no
        access row, `ValueError` for an unknown role and
        `UserCompanyAccessNotFoundError` when the access row disappears
        under a concurrent detach.
        """
        if inp.role not in CompanyRole.values():
            raise ValueError(f"Invalid company role: {inp.role!r} (expected one of {CompanyRole.values()})")

        company_id, caller_role = _resolve_caller_scope(self._authz, inp.caller_id, inp.project_id)

        access = self._access.find(inp.target_user_id, company_id)
        if access is None:
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

        return self._apply_requested_role(inp, company_id, access.role)

    def _apply_requested_role(self, inp: AssignProjectMemberInput, company_id: UUID, current_role: str) -> str:
        """Promote the target's COMPANY role when asked; never demote.

        The promotion goes through `SetMemberRoleUseCase`, so its company-admin
        guard, last-admin guard and locking apply unchanged. A caller who is
        not a company admin gets `AssignmentForbiddenError` and the whole
        request (assignment included) is rolled back by the endpoint.
        """
        if inp.role != CompanyRole.MANAGER.value or current_role != CompanyRole.MEMBER.value:
            return current_role
        if self._role_setter is None or self._db is None:
            raise AssignmentForbiddenError("Company-role promotion is not available on this deployment")
        try:
            updated = self._role_setter.execute(
                SetMemberRoleInput(
                    caller_id=inp.caller_id,
                    company_id=company_id,
                    user_id=inp.target_user_id,
                    role=CompanyRole.MANAGER.value,
                ),
                self._db,
            )
        except ForbiddenCompanyError as exc:
            raise AssignmentForbiddenError("Only a company admin can assign as manager") from exc
        return updated.role


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

        # Same order as assigning: "not a member of this company" is answered
        # with 404 for every caller, so an admin and a manager get the same
        # answer for the same state.
        if self._access.find(target_user_id, company_id) is None:
            raise TargetNotCompanyMemberError(f"user {target_user_id} is not a member of company {company_id}")

        _forbid_manager_on_a_non_member(self._authz, caller_role, target_user_id, company_id)

        self._membership.remove(target_user_id, project_id)
