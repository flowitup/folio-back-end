"""Log attendance use case."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from app.application.labor.ports import IWorkerRepository, ILaborEntryRepository
from app.domain.entities.labor_entry import LaborEntry
from app.domain.exceptions.labor_exceptions import WorkerNotFoundError


@dataclass
class LogAttendanceRequest:
    project_id: UUID
    worker_id: UUID
    date: date
    amount_override: Optional[Decimal] = None
    note: Optional[str] = None


@dataclass
class LogAttendanceResponse:
    id: str
    worker_id: str
    date: str
    amount_override: Optional[float]
    note: Optional[str]
    created_at: str


class LogAttendanceUseCase:
    """Log daily attendance for a worker."""

    def __init__(
        self,
        worker_repo: IWorkerRepository,
        entry_repo: ILaborEntryRepository,
    ):
        self._worker_repo = worker_repo
        self._entry_repo = entry_repo

    def execute(self, request: LogAttendanceRequest) -> LogAttendanceResponse:
        # Verify worker exists and belongs to project
        worker = self._worker_repo.find_by_id(request.worker_id)
        if not worker or worker.project_id != request.project_id:
            raise WorkerNotFoundError(str(request.worker_id))

        entry = LaborEntry(
            id=uuid4(),
            worker_id=request.worker_id,
            date=request.date,
            amount_override=request.amount_override,
            note=request.note.strip() if request.note else None,
            created_at=datetime.now(timezone.utc),
        )

        # DuplicateEntryError will be raised by repo if constraint violated
        saved = self._entry_repo.create(entry)

        return LogAttendanceResponse(
            id=str(saved.id),
            worker_id=str(saved.worker_id),
            date=saved.date.isoformat(),
            amount_override=float(saved.amount_override) if saved.amount_override else None,
            note=saved.note,
            created_at=saved.created_at.isoformat(),
        )
