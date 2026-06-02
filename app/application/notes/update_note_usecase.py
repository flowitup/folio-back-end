"""UpdateNoteUseCase — edit an existing project journal note."""

from __future__ import annotations

from uuid import UUID

from app.application.notes.dtos import NoteDto
from app.application.notes.exceptions import NoteNotFoundError, NotProjectMemberError
from app.application.notes.ports import (
    NoteRepositoryPort,
    ProjectMembershipReaderPort,
    TransactionalSessionPort,
)
from app.domain.entities.note import _UNSET, _Unset


class UpdateNoteUseCase:
    """Update title, description, or category on a journal note.

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

    def execute(
        self,
        *,
        actor_id: UUID,
        note_id: UUID,
        title: str | None = None,
        description: str | None | _Unset = _UNSET,
        category: str | None = None,
    ) -> NoteDto:
        """Apply updates and return the updated NoteDto.

        ``description=_UNSET`` (default) → leave description unchanged.
        ``description=None``  → clear the description.
        ``description="..."`` → replace the description.

        Raises:
            NoteNotFoundError: note_id does not exist.
            NotProjectMemberError: actor is not a member of the note's project.
            ValueError: title or description fails validation.
            InvalidCategoryError: category ∉ VALID_CATEGORIES.
        """
        note = self._note_repo.find_by_id_for_update(note_id)
        if note is None:
            raise NoteNotFoundError(f"Note {note_id} not found.")

        if not self._membership.is_member(actor_id, note.project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {note.project_id}.")

        updated_note = note.with_updates(
            title=title,
            description=description,
            category=category,
        )

        self._note_repo.save(updated_note)
        self._db.commit()
        return NoteDto.from_entity(updated_note)
