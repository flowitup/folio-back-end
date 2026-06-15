"""DeleteWorkerRateChange use case.

Hard-deletes a single rate-change row after verifying it belongs to the
expected worker (and transitively, the expected project).
"""

from dataclasses import dataclass
from uuid import UUID

from app.application.labor.ports import IWorkerRepository, IWorkerRateChangeRepository
from app.domain.exceptions.labor_exceptions import RateChangeNotFoundError, WorkerNotFoundError


@dataclass
class DeleteWorkerRateChangeRequest:
    project_id: UUID
    worker_id: UUID
    rc_id: UUID


class DeleteWorkerRateChangeUseCase:
    """Delete an effective-dated rate change.

    Raises:
        WorkerNotFoundError: worker does not exist or belongs to a different project.
        RateChangeNotFoundError: rc_id not found, or belongs to a different worker.
    """

    def __init__(
        self,
        worker_repo: IWorkerRepository,
        rate_change_repo: IWorkerRateChangeRepository,
    ) -> None:
        self._worker_repo = worker_repo
        self._rate_repo = rate_change_repo

    def execute(self, request: DeleteWorkerRateChangeRequest) -> None:
        worker = self._worker_repo.find_by_id(request.worker_id)
        if worker is None or worker.project_id != request.project_id:
            raise WorkerNotFoundError(str(request.worker_id))

        rc = self._rate_repo.find_by_id(request.rc_id)
        if rc is None or rc.worker_id != request.worker_id:
            raise RateChangeNotFoundError(str(request.rc_id))

        self._rate_repo.delete(request.rc_id)
