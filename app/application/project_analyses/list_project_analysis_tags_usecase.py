"""Use case: list the distinct tags in use across a project's analyses."""

from __future__ import annotations

from uuid import UUID

from app.application.project_analyses.exceptions import NotProjectMemberError
from app.application.project_analyses.ports import ProjectAnalysisRepositoryPort, ProjectMembershipReaderPort


class ListProjectAnalysisTagsUseCase:
    """Return every distinct tag used by a project's active analyses.

    The tag filter needs the project's whole tag vocabulary, not just the tags
    on the page currently displayed — deriving the filter options client-side
    from one page would hide any tag that only appears on a later page.

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

    def execute(self, *, actor_id: UUID, project_id: UUID) -> list[str]:
        """Return the project's distinct analysis tags.

        Raises:
            NotProjectMemberError: actor is not a member of the project.
        """
        if not self._membership.is_member(actor_id, project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {project_id}.")

        return self._repo.list_tags_for_project(project_id)
