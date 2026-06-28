"""Labor activity domain entity."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from uuid import UUID


@dataclass(slots=True)
class LaborActivity:
    """Project-level daily activity log entry.

    Multiple entries per (project_id, date) are allowed. The single required
    text field ``title`` captures one logged activity for that day; each save
    creates a distinct entry.
    """

    id: UUID
    project_id: UUID
    date: date
    title: str
    created_at: datetime
    updated_at: datetime
    created_by: Optional[UUID] = None

    def __post_init__(self) -> None:
        if not self.title or not self.title.strip():
            raise ValueError("Activity title must not be empty")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LaborActivity):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
