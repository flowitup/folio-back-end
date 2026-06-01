"""Log attendance use case."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from app.application.labor.ports import IWorkerRepository, ILaborEntryRepository
from app.application.tags.exceptions import InvalidProjectTagError
from app.domain.entities.labor_entry import LaborEntry
from app.domain.exceptions.labor_exceptions import WorkerNotFoundError


@dataclass
class LogAttendanceRequest:
    project_id: UUID
    worker_id: UUID
    date: date
    amount_override: Optional[Decimal] = None
    note: Optional[str] = None
    shift_type: Optional[str] = None  # "full" | "half" | "overtime" | None
    supplement_hours: int = 0
    tag_id: Optional[UUID] = None


@dataclass
class LogAttendanceResponse:
    id: str
    worker_id: str
    date: str
    amount_override: Optional[float]
    note: Optional[str]
    shift_type: Optional[str]
    supplement_hours: int
    created_at: str
    tag_id: Optional[str] = None


class LogAttendanceUseCase:
    """Log daily attendance for a worker."""

    def __init__(
        self,
        worker_repo: IWorkerRepository,
        entry_repo: ILaborEntryRepository,
        tag_repo=None,  # ProjectTagRepositoryPort | None
    ):
        self._worker_repo = worker_repo
        self._entry_repo = entry_repo
        self._tag_repo = tag_repo

    def execute(self, request: LogAttendanceRequest) -> LogAttendanceResponse:
        # Verify worker exists and belongs to project
        worker = self._worker_repo.find_by_id(request.worker_id)
        if not worker or worker.project_id != request.project_id:
            raise WorkerNotFoundError(str(request.worker_id))

        # Guard: tag must belong to the same project as the worker
        if request.tag_id is not None and self._tag_repo is not None:
            tag = self._tag_repo.get_by_id(request.tag_id)
            if tag is None or tag.project_id != worker.project_id:
                raise InvalidProjectTagError(f"Tag {request.tag_id} does not belong to this project")

        entry = LaborEntry(
            id=uuid4(),
            worker_id=request.worker_id,
            date=request.date,
            amount_override=request.amount_override,
            note=request.note.strip() if request.note else None,
            shift_type=request.shift_type,
            supplement_hours=request.supplement_hours,
            created_at=datetime.now(timezone.utc),
            tag_id=request.tag_id,
        )

        # DuplicateEntryError will be raised by repo if constraint violated
        saved = self._entry_repo.create(entry)

        return LogAttendanceResponse(
            id=str(saved.id),
            worker_id=str(saved.worker_id),
            date=saved.date.isoformat(),
            amount_override=float(saved.amount_override) if saved.amount_override else None,
            note=saved.note,
            shift_type=saved.shift_type,
            supplement_hours=saved.supplement_hours,
            created_at=saved.created_at.isoformat(),
            tag_id=str(saved.tag_id) if saved.tag_id is not None else None,
        )
