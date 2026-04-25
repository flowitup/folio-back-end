"""Update attendance use case."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.application.labor.ports import ILaborEntryRepository
from app.domain.exceptions.labor_exceptions import LaborEntryNotFoundError


@dataclass
class UpdateAttendanceRequest:
    entry_id: UUID
    amount_override: Optional[Decimal] = None
    note: Optional[str] = None
    shift_type: Optional[str] = None


@dataclass
class UpdateAttendanceResponse:
    id: str
    worker_id: str
    date: str
    amount_override: Optional[float]
    note: Optional[str]
    shift_type: str
    created_at: str


class UpdateAttendanceUseCase:
    """Update an existing attendance entry."""

    def __init__(self, entry_repo: ILaborEntryRepository):
        self._repo = entry_repo

    def execute(self, request: UpdateAttendanceRequest) -> UpdateAttendanceResponse:
        entry = self._repo.find_by_id(request.entry_id)
        if not entry:
            raise LaborEntryNotFoundError(str(request.entry_id))

        # Update fields (allow setting to None to clear override)
        entry.amount_override = request.amount_override
        entry.note = request.note.strip() if request.note else None
        if request.shift_type is not None:
            entry.shift_type = request.shift_type

        saved = self._repo.update(entry)

        return UpdateAttendanceResponse(
            id=str(saved.id),
            worker_id=str(saved.worker_id),
            date=saved.date.isoformat(),
            amount_override=float(saved.amount_override) if saved.amount_override else None,
            note=saved.note,
            shift_type=saved.shift_type,
            created_at=saved.created_at.isoformat(),
        )
