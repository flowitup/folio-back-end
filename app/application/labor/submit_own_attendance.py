"""Submit own attendance use case — a worker logs their own day, pending validation."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from app.application.labor.ports import ILaborEntryRepository, IWorkerRepository
from app.domain.entities.labor_entry import STATUS_PENDING, LaborEntry
from app.domain.exceptions.labor_exceptions import (
    AttendanceDateOutOfRangeError,
    WorkerNotFoundError,
    WorkerNotLinkedError,
)

# A phone ahead of server UTC (Vietnam is UTC+7, Paris UTC+1/+2) is already on
# "tomorrow" during the last hours of the UTC day; only then is the next date accepted,
# so nobody can pre-log a future shift in the middle of the day.
_FORWARD_TOLERANCE_FROM_UTC_HOUR = 16


@dataclass
class SubmitOwnAttendanceRequest:
    project_id: UUID
    user_id: UUID
    date: date
    shift_type: Optional[str] = None  # "full" | "half" | "overtime" | None
    supplement_hours: int = 0
    note: Optional[str] = None
    # How many days back a worker may still log (1 = today or yesterday).
    max_backdate_days: int = 1
    # Injected clock for tests; defaults to the server's UTC time.
    now: Optional[datetime] = None


@dataclass
class SubmitOwnAttendanceResponse:
    id: str
    worker_id: str
    worker_name: str
    date: str
    shift_type: Optional[str]
    supplement_hours: int
    note: Optional[str]
    status: str
    submitted_by_user_id: str
    created_at: str


class SubmitOwnAttendanceUseCase:
    """Create a *pending* labor entry for the worker linked to the calling user.

    The worker is resolved from ``workers.user_id`` on the target project, so a
    user can never log a day for anyone but themselves. No amount override and
    no tag: those are manager-only fields set at validation/edit time.
    """

    def __init__(self, worker_repo: IWorkerRepository, entry_repo: ILaborEntryRepository):
        self._worker_repo = worker_repo
        self._entry_repo = entry_repo

    def execute(self, request: SubmitOwnAttendanceRequest) -> SubmitOwnAttendanceResponse:
        worker = self._worker_repo.find_by_project_and_user(request.project_id, request.user_id)
        if worker is None:
            raise WorkerNotLinkedError(str(request.project_id))
        if not worker.is_active:
            raise WorkerNotFoundError(str(worker.id))

        now = request.now or datetime.now(timezone.utc)
        today = now.date()
        earliest = today - timedelta(days=max(request.max_backdate_days, 0))
        latest = today + timedelta(days=1) if now.hour >= _FORWARD_TOLERANCE_FROM_UTC_HOUR else today
        if not (earliest <= request.date <= latest):
            raise AttendanceDateOutOfRangeError(
                f"Attendance can only be logged between {earliest.isoformat()} and {latest.isoformat()}"
            )

        entry = LaborEntry(
            id=uuid4(),
            worker_id=worker.id,
            date=request.date,
            note=request.note.strip() if request.note else None,
            shift_type=request.shift_type,
            supplement_hours=request.supplement_hours,
            created_at=datetime.now(timezone.utc),
            status=STATUS_PENDING,
            submitted_by_user_id=request.user_id,
        )
        # DuplicateEntryError is raised by the repository on (worker_id, date).
        saved = self._entry_repo.create(entry)

        return SubmitOwnAttendanceResponse(
            id=str(saved.id),
            worker_id=str(saved.worker_id),
            worker_name=worker.person_name or worker.name,
            date=saved.date.isoformat(),
            shift_type=saved.shift_type,
            supplement_hours=saved.supplement_hours,
            note=saved.note,
            status=saved.status,
            submitted_by_user_id=str(request.user_id),
            created_at=saved.created_at.isoformat(),
        )
