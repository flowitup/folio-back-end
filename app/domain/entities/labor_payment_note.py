"""Labor payment note domain entity."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from uuid import UUID


@dataclass(slots=True)
class LaborPaymentNote:
    """Free-text note on one worker's labor charges for one month of a project.

    The Payments tab computes owed/paid per (worker, month) on the fly, so the
    note is keyed by that same pair rather than by a stored charge row — e.g.
    "paid the rest in cash on the 12th" or "waiting for the client's transfer".

    ``month`` is always the first day of the month. Blank notes are never
    stored: the use-case layer deletes the row instead.
    """

    id: UUID
    project_id: UUID
    worker_id: UUID
    month: date
    note: str
    created_at: datetime
    updated_at: datetime
    created_by: Optional[UUID] = None

    def __post_init__(self) -> None:
        if not self.note or not self.note.strip():
            raise ValueError("Labor payment note must not be empty")
        if self.month.day != 1:
            raise ValueError("Labor payment note month must be the first day of the month")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LaborPaymentNote):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
