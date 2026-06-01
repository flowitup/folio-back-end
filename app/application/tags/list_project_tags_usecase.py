"""ListProjectTagsUseCase — list all tags for a project."""

from __future__ import annotations

from uuid import UUID

from app.application.tags.dtos import ProjectTagDto
from app.application.tags.exceptions import NotProjectMemberError
from app.application.tags.ports import ProjectMembershipReaderPort, ProjectTagRepositoryPort


class ListProjectTagsUseCase:
    """Return all tags for a project ordered by name.

    Authorization: acting user must be a project member.
    """

    def __init__(
        self,
        tag_repo: ProjectTagRepositoryPort,
        membership_reader: ProjectMembershipReaderPort,
    ) -> None:
        self._repo = tag_repo
        self._membership = membership_reader

    def execute(self, *, actor_id: UUID, project_id: UUID) -> list[ProjectTagDto]:
        """List tags for the project.

        Raises:
            NotProjectMemberError: actor is not a member of the project.
        """
        if not self._membership.is_member(actor_id, project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {project_id}.")

        tags = self._repo.list_by_project(project_id)
        return [ProjectTagDto.from_entity(t) for t in tags]
