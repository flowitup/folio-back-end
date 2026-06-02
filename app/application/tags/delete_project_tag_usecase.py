"""DeleteProjectTagUseCase — delete a project-scoped phase tag."""

from __future__ import annotations

from uuid import UUID

from app.application.tags.exceptions import NotProjectMemberError, ProjectTagNotFoundError
from app.application.tags.ports import (
    ProjectMembershipReaderPort,
    ProjectTagRepositoryPort,
    TransactionalSessionPort,
)


class DeleteProjectTagUseCase:
    """Delete a tag. Downstream labor_entries and invoices have their tag_id SET NULL.

    Authorization: acting user must be a project member.
    """

    def __init__(
        self,
        tag_repo: ProjectTagRepositoryPort,
        membership_reader: ProjectMembershipReaderPort,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._repo = tag_repo
        self._membership = membership_reader
        self._db = db_session

    def execute(self, *, actor_id: UUID, project_id: UUID, tag_id: UUID) -> None:
        """Delete the tag and commit.

        Raises:
            NotProjectMemberError: actor is not a member.
            ProjectTagNotFoundError: tag does not exist in the project.
        """
        if not self._membership.is_member(actor_id, project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {project_id}.")

        tag = self._repo.get_by_id(tag_id)
        if tag is None or tag.project_id != project_id:
            raise ProjectTagNotFoundError(f"Tag {tag_id} not found in project {project_id}.")

        self._repo.delete(tag_id)
        self._db.commit()
