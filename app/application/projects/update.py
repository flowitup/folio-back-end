"""Update project use case."""

import re
from decimal import Decimal
from typing import Optional, Set
from uuid import UUID

from app.application.projects.ports import IProjectRepository
from app.domain.entities.project import Project
from app.domain.exceptions.project_exceptions import (
    InvalidProjectDataError,
    ProjectNotFoundError,
)

_PREFIX_RE = re.compile(r"^[A-Z0-9]{1,8}$")

# Sentinel — used to distinguish "caller did not provide this field" from
# "caller explicitly set it to None (clear the value)". Must not be a valid
# field value; only compared via `is`.
_UNSET = object()


class UpdateProjectUseCase:
    """Update an existing project.

    Only fields present in `provided_fields` are mutated. This prevents a
    PATCH of budget_source from silently wiping budget (the description-drop
    landmine pattern).
    """

    def __init__(self, project_repo: IProjectRepository):
        self._repo = project_repo

    def execute(
        self,
        project_id: UUID,
        name: Optional[str] = None,
        address: Optional[str] = None,
        invoice_prefix: Optional[str] = None,
        budget: object = _UNSET,
        budget_source: object = _UNSET,
        provided_fields: Optional[Set[str]] = None,
    ) -> Project:
        """Update the project. Only fields in provided_fields are applied.

        budget and budget_source use _UNSET as the default rather than None
        so callers can distinguish "not provided" from "set to null".
        provided_fields is a set of field names that were explicitly included
        in the request body (populated from Pydantic's model_fields_set).
        """
        project = self._repo.find_by_id(project_id)
        if not project:
            raise ProjectNotFoundError(str(project_id))

        if provided_fields is None:
            provided_fields = set()

        if name is not None:
            if len(name.strip()) == 0:
                raise InvalidProjectDataError("Project name cannot be empty")
            if len(name) > 255:
                raise InvalidProjectDataError("Project name exceeds 255 characters")
            project.name = name.strip()

        if address is not None:
            project.address = address.strip() if address else None

        if invoice_prefix is not None:
            cleaned = invoice_prefix.strip().upper()
            if cleaned == "":
                project.invoice_prefix = None
            else:
                if not _PREFIX_RE.match(cleaned):
                    raise InvalidProjectDataError("Invoice prefix must be 1-8 uppercase letters or digits (A-Z, 0-9)")
                project.invoice_prefix = cleaned

        # budget: only touch when explicitly included in the request
        if "budget" in provided_fields:
            project.budget = Decimal(str(budget)) if budget is not None else None

        # budget_source: only touch when explicitly included in the request
        if "budget_source" in provided_fields:
            project.budget_source = budget_source.strip() if budget_source else None  # type: ignore[union-attr]

        return self._repo.update(project)
