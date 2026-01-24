"""Update project use case."""

from typing import Optional
from uuid import UUID

from app.application.projects.ports import IProjectRepository
from app.domain.entities.project import Project
from app.domain.exceptions.project_exceptions import (
    ProjectNotFoundError,
    InvalidProjectDataError,
)


class UpdateProjectUseCase:
    """Update an existing project."""

    def __init__(self, project_repo: IProjectRepository):
        self._repo = project_repo

    def execute(
        self,
        project_id: UUID,
        name: Optional[str] = None,
        address: Optional[str] = None,
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

        return self._repo.update(project)
