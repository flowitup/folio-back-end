"""Delete project use case."""

from uuid import UUID

from app.application.projects.ports import IProjectRepository
from app.domain.exceptions.project_exceptions import ProjectNotFoundError


class DeleteProjectUseCase:
    """Delete a project."""

    def __init__(self, project_repo: IProjectRepository):
        self._repo = project_repo

    def execute(self, project_id: UUID) -> None:
        project = self._repo.find_by_id(project_id)
        if not project:
            raise ProjectNotFoundError(str(project_id))
        self._repo.delete(project_id)
