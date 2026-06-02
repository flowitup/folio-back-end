"""CreateProjectTagUseCase — create a new project-scoped phase tag."""

from __future__ import annotations

from app.application.tags.dtos import CreateTagDto, ProjectTagDto
from app.application.tags.exceptions import DuplicateProjectTagNameError, NotProjectMemberError
from app.application.tags.ports import (
    ProjectMembershipReaderPort,
    ProjectTagRepositoryPort,
    TransactionalSessionPort,
)
from app.domain.entities.project_tag import ProjectTag


class CreateProjectTagUseCase:
    """Create a new tag scoped to a project.

    Authorization: acting user must be a project member.
    Uniqueness: (project_id, name) must not already exist.
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

    def execute(self, dto: CreateTagDto) -> ProjectTagDto:
        """Create and persist a tag; return its DTO.

        Raises:
            NotProjectMemberError: actor is not a member of the project.
            DuplicateProjectTagNameError: name already exists in project.
            ValueError: name or color fails validation.
        """
        if not self._membership.is_member(dto.actor_id, dto.project_id):
            raise NotProjectMemberError(f"User {dto.actor_id} is not a member of project {dto.project_id}.")

        if self._repo.exists_name_in_project(dto.project_id, dto.name):
            raise DuplicateProjectTagNameError(f"Tag '{dto.name}' already exists in project {dto.project_id}.")

        tag = ProjectTag.create(
            project_id=dto.project_id,
            name=dto.name,
            color=dto.color,
        )
        self._repo.add(tag)
        self._db.commit()
        return ProjectTagDto.from_entity(tag)
