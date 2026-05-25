"""Update project use case."""

import re
from typing import Optional
from uuid import UUID

from app.application.projects.ports import IProjectRepository
from app.domain.entities.project import Project
from app.domain.exceptions.project_exceptions import (
    ProjectNotFoundError,
    InvalidProjectDataError,
)

_PREFIX_RE = re.compile(r"^[A-Z0-9]{1,8}$")


class UpdateProjectUseCase:
    """Update an existing project."""

    def __init__(self, project_repo: IProjectRepository):
        self._repo = project_repo

    def execute(
        self,
        project_id: UUID,
        name: Optional[str] = None,
        address: Optional[str] = None,
        invoice_prefix: Optional[str] = None,
    ) -> Project:
        project = self._repo.find_by_id(project_id)
        if not project:
            raise ProjectNotFoundError(str(project_id))

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

        return self._repo.update(project)
