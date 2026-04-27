"""ListProjectNotesUseCase — return all notes for a project."""

from __future__ import annotations

from uuid import UUID

from app.application.notes.dtos import NoteDto
from app.application.notes.exceptions import NotProjectMemberError
from app.application.notes.ports import (
    NoteRepositoryPort,
    ProjectMembershipReaderPort,
)


class ListProjectNotesUseCase:
    """List all notes belonging to a project, ordered by due_date ASC.

    Authorization: the acting user must be a member of the project.
    No DB session needed — read-only, no commit required.
    """

    def __init__(
        self,
        note_repo: NoteRepositoryPort,
        membership_reader: ProjectMembershipReaderPort,
    ) -> None:
        self._note_repo = note_repo
        self._membership = membership_reader

    def execute(self, *, actor_id: UUID, project_id: UUID) -> list[NoteDto]:
        """Return all notes for the project as DTOs.

        Raises:
            NotProjectMemberError: actor is not a member of the project.
        """
        if not self._membership.is_member(actor_id, project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {project_id}.")

        notes = self._note_repo.list_by_project(project_id)
        return [NoteDto.from_entity(n) for n in notes]
