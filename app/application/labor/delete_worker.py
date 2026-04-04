"""Delete worker use case (soft delete)."""

from dataclasses import dataclass
from uuid import UUID

from app.application.labor.ports import IWorkerRepository
from app.domain.exceptions.labor_exceptions import WorkerNotFoundError


@dataclass
class DeleteWorkerRequest:
    worker_id: UUID


class DeleteWorkerUseCase:
    """Soft delete a worker (set is_active=False)."""

    def __init__(self, worker_repo: IWorkerRepository):
        self._repo = worker_repo

    def execute(self, request: DeleteWorkerRequest) -> None:
        worker = self._repo.find_by_id(request.worker_id)
        if not worker:
            raise WorkerNotFoundError(str(request.worker_id))

        self._repo.soft_delete(request.worker_id)
