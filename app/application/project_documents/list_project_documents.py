"""Use case: list documents attached to a project."""

from __future__ import annotations

from uuid import UUID

from app.application.project_documents.dtos import ListFiltersDTO, ListResultDTO
from app.application.project_documents.ports import IProjectDocumentRepository


class ListProjectDocumentsUseCase:
    """Return a paginated, filtered list of documents for a given project."""

    def __init__(self, repo: IProjectDocumentRepository) -> None:
        self._repo = repo

    def execute(self, project_id: UUID, filters: ListFiltersDTO) -> ListResultDTO:
        """Delegate list query to the repository.

        Args:
            project_id: UUID of the project whose documents to list.
            filters: Pagination, kind filter, and sort options.

        Returns:
            ListResultDTO with matching items and total count.
        """
        return self._repo.list_for_project(project_id, filters)
