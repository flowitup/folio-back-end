"""MarkNoteDoneUseCase — transition a note to status='done'."""

from __future__ import annotations

from uuid import UUID

from app.application.notes.dtos import NoteDto
from app.application.notes.exceptions import NoteNotFoundError, NotProjectMemberError
from app.application.notes.ports import (
    NoteRepositoryPort,
    ProjectMembershipReaderPort,
    TransactionalSessionPort,
)


class MarkNoteDoneUseCase:
    """Mark a note as done.

    Authorization: the acting user must be a member of the note's project.
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

    def execute(self, *, actor_id: UUID, note_id: UUID) -> NoteDto:
        """Transition note to 'done'; return updated DTO.

        Raises:
            NoteNotFoundError: note_id does not exist.
            NotProjectMemberError: actor is not a member of the note's project.
        """
        note = self._note_repo.find_by_id(note_id)
        if note is None:
            raise NoteNotFoundError(f"Note {note_id} not found.")

        if not self._membership.is_member(actor_id, note.project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {note.project_id}.")

        done_note = note.mark_done()
        self._note_repo.save(done_note)
        self._db.commit()
        return NoteDto.from_entity(done_note)
