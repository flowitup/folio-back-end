"""Use case: list analyses attached to a project."""

from __future__ import annotations

from uuid import UUID

from app.application.project_analyses.dtos import AnalysisOutput, AnalysisPage, ListAnalysesInput
from app.application.project_analyses.exceptions import NotProjectMemberError
from app.application.project_analyses.ports import ProjectAnalysisRepositoryPort, ProjectMembershipReaderPort


class ListProjectAnalysesUseCase:
    """Return a paginated, filtered list of active analyses for a project.

    Authorization: the acting user must be a member of the project.
    No DB session needed — read-only, no commit required.
    """

    def __init__(
        self,
        repo: ProjectAnalysisRepositoryPort,
        membership_reader: ProjectMembershipReaderPort,
    ) -> None:
        self._repo = repo
        self._membership = membership_reader

    def execute(self, *, actor_id: UUID, filters: ListAnalysesInput) -> AnalysisPage:
        """Return the requested page of analyses as an AnalysisPage.

        Raises:
            NotProjectMemberError: actor is not a member of the project.
        """
        if not self._membership.is_member(actor_id, filters.project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {filters.project_id}.")

        items, total = self._repo.list_by_project(
            filters.project_id,
            q=filters.q,
            tags=filters.tags,
            sort=filters.sort,
            order=filters.order,
            page=filters.page,
            per_page=filters.per_page,
        )
        return AnalysisPage(
            items=[AnalysisOutput.from_entity(a) for a in items],
            total=total,
            page=filters.page,
            per_page=filters.per_page,
        )
