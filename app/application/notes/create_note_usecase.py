"""CreateNoteUseCase — create a new project journal note."""

from __future__ import annotations

from uuid import UUID

from app.application.notes.dtos import NoteDto
from app.application.notes.exceptions import NotProjectMemberError
from app.application.notes.ports import (
    NoteRepositoryPort,
    ProjectMembershipReaderPort,
    TransactionalSessionPort,
)
from app.domain.entities.note import Note


class CreateNoteUseCase:
    """Create a new journal note scoped to a project.

    Authorization: the acting user must be a member of the project.
    """

    def __init__(
        self,
        note_repo: NoteRepositoryPort,
        membership_reader: ProjectMembershipReaderPort,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._note_repo = note_repo
        self._membership = membership_reader
        self._db = db_session

    def execute(
        self,
        *,
        actor_id: UUID,
        project_id: UUID,
        title: str,
        description: str | None,
        category: str = "general",
    ) -> NoteDto:
        """Create and persist a journal note; return its DTO.

        Raises:
            NotProjectMemberError: actor is not a member of the project.
            ValueError: title or description fails validation.
            InvalidCategoryError: category ∉ VALID_CATEGORIES.
        """
        if not self._membership.is_member(actor_id, project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {project_id}.")

        note = Note.create(
            project_id=project_id,
            created_by=actor_id,
            title=title,
            description=description,
            category=category,
        )
        self._note_repo.add(note)
        self._db.commit()
        return NoteDto.from_entity(note)
