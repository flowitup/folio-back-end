"""List pending attendance use case — bell payload for managers."""

from dataclasses import dataclass
from typing import List, Optional
from uuid import UUID

from app.application.labor.ports import IPendingAttendanceQuery

_PENDING_ATTENDANCE_HARD_CAP = 100


@dataclass
class PendingAttendanceDto:
    entry_id: str
    project_id: str
    project_name: str
    worker_id: str
    worker_name: str
    date: str
    shift_type: Optional[str]
    supplement_hours: int
    note: Optional[str]
    submitted_at: str
    kind: str = "attendance_pending"
    proposed_shift_type: Optional[str] = None
    proposed_supplement_hours: Optional[int] = None
    proposed_note: Optional[str] = None


class ListPendingAttendanceUseCase:
    """Pending worker-submitted days the caller may validate, newest first, capped at 100.

    ``company_id``/``project_id`` are optional filters forwarded straight to the query
    port, so a caller scoped to one company or project gets that scope's own 100, not
    the first 100 across every company/project it may validate with the scope applied
    afterwards."""

    def __init__(self, query: IPendingAttendanceQuery) -> None:
        self._query = query

    def execute(
        self, *, user_id: UUID, company_id: Optional[UUID] = None, project_id: Optional[UUID] = None
    ) -> List[PendingAttendanceDto]:
        items = self._query.list_pending_for_validator(
            user_id=user_id,
            limit=_PENDING_ATTENDANCE_HARD_CAP,
            company_id=company_id,
            project_id=project_id,
        )
        return [
            PendingAttendanceDto(
                entry_id=str(i.entry_id),
                project_id=str(i.project_id),
                project_name=i.project_name,
                worker_id=str(i.worker_id),
                worker_name=i.worker_name,
                date=i.date.isoformat(),
                shift_type=i.shift_type,
                supplement_hours=i.supplement_hours,
                note=i.note,
                submitted_at=i.submitted_at.isoformat(),
                kind=i.kind,
                proposed_shift_type=i.proposed_shift_type,
                proposed_supplement_hours=i.proposed_supplement_hours,
                proposed_note=i.proposed_note,
            )
            for i in items
        ]
