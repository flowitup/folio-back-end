"""A worker's labour role must belong to the company of the worker's project."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from app.domain.exceptions.labor_exceptions import InvalidWorkerDataError


def assert_role_in_project_company(
    labor_role_repo: Optional[Any],
    authz_reader: Optional[Any],
    role_id: Optional[UUID],
    project_id: UUID,
) -> None:
    """Refuse a role that is unknown, legacy (no company) or owned by another company.

    Same rule as a member's pay defaults: roles are company-scoped, and a
    foreign one would both cross tenants and echo that company's role name
    and colour back. Without the repositories wired (unit tests that build
    the use case with the worker repository only) there is no check.
    """
    if role_id is None or labor_role_repo is None or authz_reader is None:
        return
    role = labor_role_repo.find_by_id(role_id)
    company_id = authz_reader.project_company_id(project_id)
    if role is None or role.company_id is None or role.company_id != company_id:
        raise InvalidWorkerDataError("Labor role not found")
