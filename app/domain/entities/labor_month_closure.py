"""Labor month closure ("Chốt tháng") domain entities.

A manager closes a project's month once its wages are settled. Closing is a
soft marker, not a lock: every write into the month still succeeds, but each one
is recorded as a :class:`LaborMonthClosureEdit` (who, when, what kind of change,
which entry/worker) so a late day or a moved payment no longer goes unnoticed.

``month`` is always the first day of the month.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from uuid import UUID

# What kind of write touched a closed month. Stored as plain strings so a new
# kind never needs a migration; clients fall back to a generic label.
EDIT_KINDS: frozenset[str] = frozenset(
    {
        "entry_created",  # a manager logged a day (single or bulk)
        "entry_updated",  # a manager edited a day
        "entry_deleted",  # a manager deleted a day
        "entry_self_logged",  # a worker logged their own day (pending)
        "entry_self_edited",  # a worker edited their own day / asked for a change
        "entry_validated",  # a pending day was validated
        "entry_rejected",  # a pending day was refused (deleted)
        "change_approved",  # a worker's change request was applied
        "payment_created",  # a labor payment was recorded for the month
        "payment_updated",  # a labor payment of (or moved into / out of) the month was edited
        "payment_deleted",  # a labor payment of the month was deleted
        "rate_changed",  # a daily-rate change dated into the month was set
        "rate_change_deleted",  # a daily-rate change dated into the month was removed
    }
)


@dataclass(slots=True)
class LaborMonthClosure:
    """One closed (project, month)."""

    id: UUID
    project_id: UUID
    month: date
    closed_at: datetime
    closed_by: Optional[UUID] = None

    def __post_init__(self) -> None:
        if self.month.day != 1:
            raise ValueError("Closed month must be the first day of the month")


@dataclass(slots=True)
class LaborMonthClosureEdit:
    """One write recorded against a closed month."""

    id: UUID
    closure_id: UUID
    kind: str
    edited_at: datetime
    user_id: Optional[UUID] = None
    entry_id: Optional[UUID] = None  # labor entry, labor payment (invoice) or rate change id
    worker_id: Optional[UUID] = None
    entry_date: Optional[date] = None  # the attendance day / rate effective date, when there is one

    def __post_init__(self) -> None:
        if self.kind not in EDIT_KINDS:
            raise ValueError(f"Unknown closed-month edit kind: {self.kind}")
