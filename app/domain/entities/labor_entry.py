"""Labor entry domain entity."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID


@dataclass(slots=True)
class LaborEntry:
    """
    Labor entry entity.

    Represents a single day's attendance for a worker.
    UNIQUE constraint on (worker_id, date) enforced at DB level.
    """
    id: UUID
    worker_id: UUID
    date: date
    created_at: datetime
    amount_override: Optional[Decimal] = None
    note: Optional[str] = None
    shift_type: str = "full"  # "full" | "half" | "overtime"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LaborEntry):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
