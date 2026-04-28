"""Labor entry domain entity."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.domain.exceptions.labor_exceptions import InvalidLaborEntryError
from app.domain.labor.shift_multipliers import SHIFT_MULTIPLIERS


@dataclass(slots=True)
class LaborEntry:
    """
    Labor entry entity.

    Represents a single day's attendance for a worker.
    UNIQUE constraint on (worker_id, date) enforced at DB level.

    Rules:
    - shift_type is optional (None when entry is supplement-only).
    - supplement_hours must be in [0, 12].
    - At least one of (shift_type, supplement_hours > 0) must be set.
    - amount_override is only meaningful when shift_type is not None.
    """

    id: UUID
    worker_id: UUID
    date: date
    created_at: datetime
    amount_override: Optional[Decimal] = None
    note: Optional[str] = None
    shift_type: Optional[str] = None  # "full" | "half" | "overtime" | None
    supplement_hours: int = 0

    def __post_init__(self) -> None:
        """Validate domain invariants."""
        if not isinstance(self.supplement_hours, int):
            raise InvalidLaborEntryError("supplement_hours must be an integer")
        if not (0 <= self.supplement_hours <= 12):
            raise InvalidLaborEntryError(f"supplement_hours must be 0..12, got {self.supplement_hours}")
        if self.shift_type is None and self.supplement_hours == 0:
            raise InvalidLaborEntryError("Empty entry: must set shift_type or supplement_hours > 0")
        if self.shift_type is None and self.amount_override is not None:
            raise InvalidLaborEntryError("amount_override requires a shift_type")

    def effective_cost(self, daily_rate: Decimal) -> Decimal:
        """
        Compute the priced cost for this entry.

        Returns Decimal('0') for supplement-only rows (shift_type is None).
        Otherwise, amount_override wins; else daily_rate * shift multiplier.
        """
        if self.shift_type is None:
            return Decimal("0")
        if self.amount_override is not None:
            return self.amount_override
        multiplier = Decimal(str(SHIFT_MULTIPLIERS.get(self.shift_type, 1.0)))
        return daily_rate * multiplier

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LaborEntry):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
