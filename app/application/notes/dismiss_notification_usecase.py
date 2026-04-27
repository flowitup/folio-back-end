"""DismissNotificationUseCase — mark a note notification as dismissed for a user."""

from __future__ import annotations

from uuid import UUID

from app.application.notes.exceptions import NoteNotFoundError, NotProjectMemberError
from app.application.notes.ports import (
    NoteDismissalRepositoryPort,
    NoteRepositoryPort,
    ProjectMembershipReaderPort,
    TransactionalSessionPort,
)


class DismissNotificationUseCase:
    """Record that a user has dismissed a note's due notification.

    Authorization: the user must be a member of the note's project.
    Dismissal is idempotent — calling it twice for the same (user, note) pair
    is safe (the infrastructure layer uses INSERT … ON CONFLICT DO NOTHING).
    """

    def __init__(
        self,
        note_repo: NoteRepositoryPort,
        dismissal_repo: NoteDismissalRepositoryPort,
        membership_reader: ProjectMembershipReaderPort,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._note_repo = note_repo
        self._dismissal_repo = dismissal_repo
        self._membership = membership_reader
        self._db = db_session

    def execute(self, *, actor_id: UUID, note_id: UUID) -> None:
        """Dismiss the notification for *actor_id* on *note_id*.

        Raises:
            NoteNotFoundError: note_id does not exist.
            NotProjectMemberError: actor is not a member of the note's project.
        """
        note = self._note_repo.find_by_id(note_id)
        if note is None:
            raise NoteNotFoundError(f"Note {note_id} not found.")

        if not self._membership.is_member(actor_id, note.project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {note.project_id}.")

        self._dismissal_repo.add(actor_id, note_id)
        self._db.commit()
