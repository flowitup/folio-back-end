"""Labor day description domain entity."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from uuid import UUID


@dataclass(slots=True)
class LaborDayDescription:
    """Project-level daily description — one entry per (project_id, date).

    Separate from LaborActivity (per-day title) and from LaborEntry.note
    (per-worker). Captures a free-text description of the labor-charge day
    (e.g. site conditions, blockers, milestones).

    Upsert semantics: saving twice on the same date updates the existing
    entry rather than creating a duplicate. Blank/whitespace-only description
    triggers deletion of the row (handled in the use-case layer).
    """

    id: UUID
    project_id: UUID
    date: date
    description: str
    created_at: datetime
    updated_at: datetime
    created_by: Optional[UUID] = None

    def __post_init__(self) -> None:
        if not self.description or not self.description.strip():
            raise ValueError("Day description must not be empty")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LaborDayDescription):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
