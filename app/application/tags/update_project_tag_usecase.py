"""UpdateProjectTagUseCase — update name and/or color of a project tag."""

from __future__ import annotations

from app.application.tags.dtos import ProjectTagDto, UpdateTagDto
from app.application.tags.exceptions import (
    DuplicateProjectTagNameError,
    NotProjectMemberError,
    ProjectTagNotFoundError,
)
from app.application.tags.ports import (
    ProjectMembershipReaderPort,
    ProjectTagRepositoryPort,
    TransactionalSessionPort,
)


class UpdateProjectTagUseCase:
    """Update name and/or color of an existing tag.

    Authorization: acting user must be a project member.
    Uniqueness: new name (if changed) must not collide within the project.
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

    def execute(self, dto: UpdateTagDto) -> ProjectTagDto:
        """Update tag fields; return updated DTO.

        Raises:
            NotProjectMemberError: actor is not a member.
            ProjectTagNotFoundError: tag does not exist in the project.
            DuplicateProjectTagNameError: new name already in use.
            ValueError: name or color fails entity validation.
        """
        if not self._membership.is_member(dto.actor_id, dto.project_id):
            raise NotProjectMemberError(f"User {dto.actor_id} is not a member of project {dto.project_id}.")

        tag = self._repo.get_by_id(dto.tag_id)
        if tag is None or tag.project_id != dto.project_id:
            raise ProjectTagNotFoundError(f"Tag {dto.tag_id} not found in project {dto.project_id}.")

        # Uniqueness check: only when name is changing.
        if dto.name is not None and dto.name.strip() != tag.name:
            if self._repo.exists_name_in_project(dto.project_id, dto.name, exclude_tag_id=dto.tag_id):
                raise DuplicateProjectTagNameError(f"Tag '{dto.name}' already exists in project {dto.project_id}.")

        updated = tag.with_updates(name=dto.name, color=dto.color)
        self._repo.save(updated)
        self._db.commit()
        return ProjectTagDto.from_entity(updated)
