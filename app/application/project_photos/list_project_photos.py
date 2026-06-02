"""Use case: list progress photos for a project."""

from __future__ import annotations

from uuid import UUID

from app.application.project_photos.dtos import PhotoListResult
from app.application.project_photos.ports import IProjectPhotoRepository


class ListProjectPhotosUseCase:
    """Return a paginated list of active photos for a given project."""

    def __init__(self, repo: IProjectPhotoRepository) -> None:
        self._repo = repo

    def execute(self, project_id: UUID, page: int, per_page: int) -> PhotoListResult:
        """Delegate list query to the repository.

        Args:
            project_id: UUID of the project whose photos to list.
            page: 1-based page number.
            per_page: Items per page.

        Returns:
            PhotoListResult with matching items (captured_at DESC) and total count.
        """
        items, total = self._repo.list_for_project(project_id, page, per_page)
        return PhotoListResult(items=items, total=total)
