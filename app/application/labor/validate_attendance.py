"""Validate / reject attendance use cases — a manager settles a worker-submitted day."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.application.labor.ports import ILaborEntryRepository, IWorkerRepository
from app.domain.entities.labor_entry import LaborEntry
from app.domain.exceptions.labor_exceptions import (
    AttendanceAlreadyValidatedError,
    LaborEntryNotFoundError,
)

logger = logging.getLogger(__name__)


@dataclass
class ValidateAttendanceRequest:
    entry_id: UUID
    # project_id from the URL path scopes the lookup (cross-project IDOR guard).
    project_id: UUID
    validator_user_id: UUID


@dataclass
class ValidateAttendanceResponse:
    id: str
    worker_id: str
    date: str
    status: str
    validated_by_user_id: Optional[str]
    validated_at: Optional[str]


@dataclass
class RejectAttendanceRequest:
    entry_id: UUID
    project_id: UUID
    # Recorded in the server log: rejection deletes the row, so this is the audit trail.
    actor_user_id: Optional[UUID] = None


def _load_scoped_entry(
    entry_repo: ILaborEntryRepository, worker_repo: IWorkerRepository, entry_id: UUID, project_id: UUID
) -> LaborEntry:
    entry = entry_repo.find_by_id(entry_id)
    if entry is None:
        raise LaborEntryNotFoundError(str(entry_id))
    worker = worker_repo.find_by_id(entry.worker_id)
    if worker is None or worker.project_id != project_id:
        raise LaborEntryNotFoundError(str(entry_id))
    return entry


class ValidateAttendanceUseCase:
    """Flip a pending entry to validated. Idempotent: validating twice is a 200 no-op."""

    def __init__(self, entry_repo: ILaborEntryRepository, worker_repo: IWorkerRepository):
        self._entries = entry_repo
        self._workers = worker_repo

    def execute(self, request: ValidateAttendanceRequest) -> ValidateAttendanceResponse:
        entry = _load_scoped_entry(self._entries, self._workers, request.entry_id, request.project_id)
        if entry.is_pending:
            entry.validate(by_user_id=request.validator_user_id, at=datetime.now(timezone.utc))
            entry = self._entries.update(entry)
        return ValidateAttendanceResponse(
            id=str(entry.id),
            worker_id=str(entry.worker_id),
            date=entry.date.isoformat(),
            status=entry.status,
            validated_by_user_id=str(entry.validated_by_user_id) if entry.validated_by_user_id else None,
            validated_at=entry.validated_at.isoformat() if entry.validated_at else None,
        )


class RejectAttendanceUseCase:
    """Delete a pending entry. A validated entry is not rejectable (use the normal delete)."""

    def __init__(self, entry_repo: ILaborEntryRepository, worker_repo: IWorkerRepository):
        self._entries = entry_repo
        self._workers = worker_repo

    def execute(self, request: RejectAttendanceRequest) -> None:
        entry = _load_scoped_entry(self._entries, self._workers, request.entry_id, request.project_id)
        if not entry.is_pending:
            raise AttendanceAlreadyValidatedError(str(entry.id))
        logger.info(
            "attendance rejected entry=%s worker=%s date=%s submitted_by=%s rejected_by=%s",
            entry.id,
            entry.worker_id,
            entry.date.isoformat(),
            entry.submitted_by_user_id,
            request.actor_user_id,
        )
        self._entries.delete(entry.id)
