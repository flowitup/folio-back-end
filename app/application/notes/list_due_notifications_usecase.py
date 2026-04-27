"""ListDueNotificationsUseCase — return notes whose reminders are due for a user.

Hard cap
--------
Results are capped at 100 items (enforced by both the use-case and the repo
port). This protects the polling endpoint from unbounded query results if many
notes accumulate without dismissal. The cap is intentionally conservative and
can be raised in a future version if pagination is added.

Clock injection
---------------
The ``now`` parameter (defaults to ``datetime.now(UTC)``) is injected so that
tests can pin the clock without monkeypatching.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.application.notes.dtos import DueNotificationDto, NoteDto
from app.application.notes.ports import NoteQueryPort

_DUE_NOTIFICATIONS_HARD_CAP = 100


class ListDueNotificationsUseCase:
    """Return open, non-dismissed notes whose fire_at has passed for *user_id*.

    No membership check is needed here — the query port already filters by
    project membership (only notes from the user's projects are returned).
    """

    def __init__(self, note_query: NoteQueryPort) -> None:
        self._note_query = note_query

    def execute(
        self,
        *,
        user_id: UUID,
        now: datetime | None = None,
    ) -> list[DueNotificationDto]:
        """Return due notifications capped at 100.

        Args:
            user_id: The requesting user.
            now: Clock override for testability. Defaults to datetime.now(UTC).

        Returns:
            Up to 100 DueNotificationDto items, ordered by due_date ASC.
        """
        effective_now = now if now is not None else datetime.now(timezone.utc)
        notes = self._note_query.list_due_for_user(
            user_id=user_id,
            now=effective_now,
            limit=_DUE_NOTIFICATIONS_HARD_CAP,
        )
        return [DueNotificationDto(note=NoteDto.from_entity(n), dismissed=False) for n in notes]
