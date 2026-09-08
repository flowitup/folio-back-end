"""DeleteNoteUseCase — permanently remove a project note."""

from __future__ import annotations

from uuid import UUID

from app.application.notes.exceptions import NoteNotFoundError, NotProjectMemberError
from app.application.notes.ports import (
    NoteRepositoryPort,
    ProjectMembershipReaderPort,
    TransactionalSessionPort,
)


class DeleteNoteUseCase:
    """Delete a note by ID.

    Authorization: the route resolves ``project:update`` on the project in the
    URL; this use-case checks that the note actually belongs to that project
    and that the actor may read it.
    Dismissal rows are removed by FK cascade at the database level.
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

    def execute(self, *, actor_id: UUID, note_id: UUID, expected_project_id: UUID) -> None:
        """Delete the note; raise if not found or actor lacks membership.

        ``expected_project_id`` is the project named in the URL: a note of
        another project is reported as missing, so write rights on one project
        can never be spent on another project's note (the sibling document,
        photo and analysis use-cases carry the same guard).

        Raises:
            NoteNotFoundError: note_id does not exist, or belongs to a project
                other than ``expected_project_id``.
            NotProjectMemberError: actor is not a member of the note's project.
        """
        note = self._note_repo.find_by_id(note_id)
        if note is None or note.project_id != expected_project_id:
            raise NoteNotFoundError(f"Note {note_id} not found.")

        if not self._membership.is_member(actor_id, note.project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {note.project_id}.")

        self._note_repo.delete(note_id)
        self._db.commit()
