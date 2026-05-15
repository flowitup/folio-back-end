"""Delete attendance use case."""

from dataclasses import dataclass
from uuid import UUID

from app.application.labor.ports import ILaborEntryRepository, IWorkerRepository
from app.domain.exceptions.labor_exceptions import LaborEntryNotFoundError


@dataclass
class DeleteAttendanceRequest:
    entry_id: UUID
    # project_id from the URL path scopes the lookup so callers cannot
    # delete an entry whose worker belongs to a different project.
    project_id: UUID


class DeleteAttendanceUseCase:
    """Delete an attendance entry."""

    def __init__(self, entry_repo: ILaborEntryRepository, worker_repo: IWorkerRepository):
        self._repo = entry_repo
        self._workers = worker_repo

    def execute(self, request: DeleteAttendanceRequest) -> None:
        entry = self._repo.find_by_id(request.entry_id)
        if not entry:
            raise LaborEntryNotFoundError(str(request.entry_id))

        # Cross-project IDOR guard: the entry's worker must belong to the
        # project supplied via the URL path.
        worker = self._workers.find_by_id(entry.worker_id)
        if worker is None or worker.project_id != request.project_id:
            raise LaborEntryNotFoundError(str(request.entry_id))

        self._repo.delete(request.entry_id)
