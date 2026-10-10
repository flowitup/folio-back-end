"""Delete worker use case (soft delete)."""

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from app.application.labor.ports import IWorkerRepository
from app.domain.exceptions.labor_exceptions import WorkerNotFoundError


@dataclass
class DeleteWorkerRequest:
    worker_id: UUID
    # The project the caller was authorised for; a worker of another project is "not found".
    project_id: UUID


class DeleteWorkerUseCase:
    """Soft delete a worker (set is_active=False)."""

    def __init__(self, worker_repo: IWorkerRepository):
        self._repo = worker_repo

    def execute(self, request: DeleteWorkerRequest) -> Optional[UUID]:
        """Soft delete the worker; returns their person id so the caller can stop them company-wide."""
        worker = self._repo.find_by_id(request.worker_id)
        if worker is None or worker.project_id != request.project_id:
            raise WorkerNotFoundError(str(request.worker_id))

        self._repo.soft_delete(worker.id)
        return worker.person_id
