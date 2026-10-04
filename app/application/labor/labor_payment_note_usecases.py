"""Use cases for labor payment notes.

One note per (project_id, worker_id, month), shown on the worker's row of the
labor Payments tab. SetLaborPaymentNoteUseCase upserts:
  - If note.strip() is empty → delete the row and return None.
  - If a note already exists for the key, update its text.
  - Otherwise create a new note.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import List, Optional
from uuid import UUID, uuid4

from app.application.labor.ports import ILaborPaymentNoteRepository, IWorkerRepository
from app.domain.entities.labor_payment_note import LaborPaymentNote


class LaborPaymentNoteWorkerNotFound(Exception):
    """The worker does not exist or belongs to another project."""


@dataclass
class LaborPaymentNoteDetail:
    """Output DTO for a single labor payment note."""

    id: UUID
    project_id: UUID
    worker_id: UUID
    month: str  # YYYY-MM
    note: str
    created_by: Optional[str]
    created_at: str
    updated_at: str


def _to_detail(n: LaborPaymentNote) -> LaborPaymentNoteDetail:
    return LaborPaymentNoteDetail(
        id=n.id,
        project_id=n.project_id,
        worker_id=n.worker_id,
        month=n.month.strftime("%Y-%m"),
        note=n.note,
        created_by=str(n.created_by) if n.created_by else None,
        created_at=n.created_at.isoformat() if n.created_at else "",
        updated_at=n.updated_at.isoformat() if n.updated_at else "",
    )


@dataclass
class SetLaborPaymentNoteRequest:
    """Upsert-or-delete request. ``month`` is any day of the target month."""

    project_id: UUID
    worker_id: UUID
    month: date
    note: str
    created_by: Optional[UUID] = None


@dataclass
class ListLaborPaymentNotesRequest:
    project_id: UUID
    month: Optional[date] = None


class SetLaborPaymentNoteUseCase:
    """Upsert (or clear) the note on one worker's month.

    Raises LaborPaymentNoteWorkerNotFound when the worker is not on the project,
    so a note can never point at another project's worker.
    """

    def __init__(self, repo: ILaborPaymentNoteRepository, worker_repo: IWorkerRepository) -> None:
        self._repo = repo
        self._worker_repo = worker_repo

    def execute(self, req: SetLaborPaymentNoteRequest) -> Optional[LaborPaymentNoteDetail]:
        worker = self._worker_repo.find_by_id(req.worker_id)
        if worker is None or worker.project_id != req.project_id:
            raise LaborPaymentNoteWorkerNotFound(str(req.worker_id))

        month = req.month.replace(day=1)
        stripped = req.note.strip()
        if not stripped:
            self._repo.delete(req.project_id, req.worker_id, month)
            return None

        now = datetime.now(timezone.utc)
        existing = self._repo.find(req.project_id, req.worker_id, month)
        if existing is not None:
            # Keep the original creator and created_at.
            existing.note = stripped
            existing.updated_at = now
            return _to_detail(self._repo.upsert(existing))

        entity = LaborPaymentNote(
            id=uuid4(),
            project_id=req.project_id,
            worker_id=req.worker_id,
            month=month,
            note=stripped,
            created_by=req.created_by,
            created_at=now,
            updated_at=now,
        )
        return _to_detail(self._repo.upsert(entity))


class ListLaborPaymentNotesUseCase:
    """List a project's labor payment notes, optionally for a single month."""

    def __init__(self, repo: ILaborPaymentNoteRepository) -> None:
        self._repo = repo

    def execute(self, req: ListLaborPaymentNotesRequest) -> List[LaborPaymentNoteDetail]:
        month = req.month.replace(day=1) if req.month else None
        notes = self._repo.list_by_project(req.project_id, month)
        notes.sort(key=lambda n: (n.month, str(n.worker_id)))
        return [_to_detail(n) for n in notes]
