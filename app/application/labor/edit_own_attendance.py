"""Edit own attendance use cases — a worker corrects a past day, a manager settles the change.

- A *pending* day (not yet validated) is simply updated in place; it stays pending.
- A *validated* day keeps its priced values and gains a proposal (``proposed_*``); the
  managers of the project see it in their bell and either apply it (validate) or drop
  it (reject). Either way the worker's day is never unpriced while the request is open.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.application.labor.ports import ILaborEntryRepository, IWorkerRepository
from app.application.labor.validate_attendance import _load_scoped_entry
from app.domain.entities.labor_entry import LaborEntry
from app.domain.exceptions.labor_exceptions import (
    LaborEntryNotFoundError,
    NoChangeRequestError,
    WorkerNotFoundError,
    WorkerNotLinkedError,
)

logger = logging.getLogger(__name__)


@dataclass
class EditOwnAttendanceRequest:
    project_id: UUID
    user_id: UUID
    entry_id: UUID
    shift_type: Optional[str] = None
    supplement_hours: int = 0
    note: Optional[str] = None


@dataclass
class OwnAttendanceEntryResponse:
    """The worker's day after an edit or a manager's decision."""

    id: str
    worker_id: str
    date: str
    status: str
    shift_type: Optional[str]
    supplement_hours: int
    note: Optional[str]
    change_pending: bool
    proposed_shift_type: Optional[str]
    proposed_supplement_hours: Optional[int]
    proposed_note: Optional[str]
    change_requested_at: Optional[str]

    @classmethod
    def from_entry(cls, entry: LaborEntry) -> "OwnAttendanceEntryResponse":
        return cls(
            id=str(entry.id),
            worker_id=str(entry.worker_id),
            date=entry.date.isoformat(),
            status=entry.status,
            shift_type=entry.shift_type,
            supplement_hours=entry.supplement_hours,
            note=entry.note,
            change_pending=entry.has_change_request,
            proposed_shift_type=entry.proposed_shift_type,
            proposed_supplement_hours=entry.proposed_supplement_hours,
            proposed_note=entry.proposed_note,
            change_requested_at=entry.change_requested_at.isoformat() if entry.change_requested_at else None,
        )


class EditOwnAttendanceUseCase:
    """Worker-side edit of one of their own days (see module docstring)."""

    def __init__(self, worker_repo: IWorkerRepository, entry_repo: ILaborEntryRepository):
        self._workers = worker_repo
        self._entries = entry_repo

    def execute(self, request: EditOwnAttendanceRequest) -> OwnAttendanceEntryResponse:
        worker = self._workers.find_by_project_and_user(request.project_id, request.user_id)
        if worker is None:
            raise WorkerNotLinkedError(str(request.project_id))
        if not worker.is_active:
            raise WorkerNotFoundError(str(worker.id))
        entry = self._entries.find_by_id(request.entry_id)
        # Another worker's day is invisible to this account (404, not 403).
        if entry is None or entry.worker_id != worker.id:
            raise LaborEntryNotFoundError(str(request.entry_id))

        note = request.note.strip() if request.note else None
        if entry.is_pending:
            entry.shift_type = request.shift_type
            entry.supplement_hours = request.supplement_hours
            entry.note = note
            entry.check_invariants()
        else:
            entry.propose_change(
                shift_type=request.shift_type,
                supplement_hours=request.supplement_hours,
                note=note,
                by_user_id=request.user_id,
                at=datetime.now(timezone.utc),
            )
        return OwnAttendanceEntryResponse.from_entry(self._entries.update(entry))


@dataclass
class DecideAttendanceChangeRequest:
    entry_id: UUID
    project_id: UUID
    actor_user_id: UUID
    approve: bool


class DecideAttendanceChangeUseCase:
    """Manager applies (validate) or drops (reject) a worker's open change request."""

    def __init__(self, entry_repo: ILaborEntryRepository, worker_repo: IWorkerRepository):
        self._entries = entry_repo
        self._workers = worker_repo

    def execute(self, request: DecideAttendanceChangeRequest) -> OwnAttendanceEntryResponse:
        entry = _load_scoped_entry(self._entries, self._workers, request.entry_id, request.project_id)
        if not entry.has_change_request:
            raise NoChangeRequestError(str(entry.id))
        if request.approve:
            entry.apply_change(by_user_id=request.actor_user_id, at=datetime.now(timezone.utc))
        else:
            logger.info(
                "attendance change rejected entry=%s worker=%s date=%s requested_by=%s rejected_by=%s",
                entry.id,
                entry.worker_id,
                entry.date.isoformat(),
                entry.change_requested_by_user_id,
                request.actor_user_id,
            )
            entry.discard_change()
        return OwnAttendanceEntryResponse.from_entry(self._entries.update(entry))
