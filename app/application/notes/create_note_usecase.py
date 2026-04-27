"""CreateNoteUseCase — create a new project note."""

from __future__ import annotations

from datetime import date
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
    """Create a new note scoped to a project.

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
        due_date: date,
        lead_time_minutes: int = 0,
    ) -> NoteDto:
        """Create and persist a note; return its DTO.

        Raises:
            NotProjectMemberError: actor is not a member of the project.
            ValueError: title or description fails validation.
            InvalidLeadTimeError: lead_time_minutes ∉ {0, 60, 1440}.
        """
        if not self._membership.is_member(actor_id, project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {project_id}.")

        note = Note.create(
            project_id=project_id,
            created_by=actor_id,
            title=title,
            description=description,
            due_date=due_date,
            lead_time_minutes=lead_time_minutes,
        )
        self._note_repo.add(note)
        self._db.commit()
        return NoteDto.from_entity(note)
