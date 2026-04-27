"""UpdateNoteUseCase — edit an existing project note.

Dismissal-cascade invariant
----------------------------
When *due_date* or *lead_time_minutes* changes, all per-user dismissal records
for that note are deleted within the **same transaction** before the updated
note is saved.  This ensures that users who had previously dismissed the
reminder will see the re-scheduled notification again as a fresh alert.

Transaction order:
    1. Load existing note (404 if absent).
    2. Membership check.
    3. Apply field updates → new Note instance.
    4. If due_date or lead_time_minutes changed → dismissal_repo.delete_all_for_note().
    5. note_repo.save(updated_note).
    6. db_session.commit().
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.application.notes.dtos import NoteDto
from app.application.notes.exceptions import NoteNotFoundError, NotProjectMemberError
from app.application.notes.ports import (
    NoteDismissalRepositoryPort,
    NoteRepositoryPort,
    ProjectMembershipReaderPort,
    TransactionalSessionPort,
)


class UpdateNoteUseCase:
    """Update title, description, due_date, or lead_time_minutes on a note.

    Authorization: the acting user must be a member of the note's project.
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

    def execute(
        self,
        *,
        actor_id: UUID,
        note_id: UUID,
        title: str | None = None,
        description: str | None = None,
        due_date: date | None = None,
        lead_time_minutes: int | None = None,
    ) -> NoteDto:
        """Apply updates and return the updated NoteDto.

        Raises:
            NoteNotFoundError: note_id does not exist.
            NotProjectMemberError: actor is not a member of the note's project.
            ValueError: title or description fails validation.
            InvalidLeadTimeError: lead_time_minutes ∉ {0, 60, 1440}.
        """
        note = self._note_repo.find_by_id(note_id)
        if note is None:
            raise NoteNotFoundError(f"Note {note_id} not found.")

        if not self._membership.is_member(actor_id, note.project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {note.project_id}.")

        schedule_changed = (due_date is not None and due_date != note.due_date) or (
            lead_time_minutes is not None and lead_time_minutes != note.lead_time_minutes
        )

        updated_note = note.with_updates(
            title=title,
            due_date=due_date,
            lead_time_minutes=lead_time_minutes,
        )

        # Dismissal-cascade: clear all dismissals so re-scheduled reminders
        # fire again for all project members (same TX as the note save).
        if schedule_changed:
            self._dismissal_repo.delete_all_for_note(note_id)

        self._note_repo.save(updated_note)
        self._db.commit()
        return NoteDto.from_entity(updated_note)
