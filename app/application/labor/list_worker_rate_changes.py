"""ListWorkerRateChanges use case.

Returns all effective-dated rate changes for a worker, ordered effective_date DESC.
Project-scope guard mirrors SetWorkerRateChangeUseCase.
"""

from dataclasses import dataclass
from typing import List
from uuid import UUID

from app.application.labor.ports import IWorkerRepository, IWorkerRateChangeRepository
from app.application.labor.set_worker_rate_change import RateChangeDTO, _to_dto
from app.domain.exceptions.labor_exceptions import WorkerNotFoundError


@dataclass
class ListWorkerRateChangesRequest:
    project_id: UUID
    worker_id: UUID


class ListWorkerRateChangesUseCase:
    """List all rate changes for a worker, newest effective date first.

    Raises:
        WorkerNotFoundError: worker does not exist or belongs to a different project.
    """

    def __init__(
        self,
        worker_repo: IWorkerRepository,
        rate_change_repo: IWorkerRateChangeRepository,
    ) -> None:
        self._worker_repo = worker_repo
        self._rate_repo = rate_change_repo

    def execute(self, request: ListWorkerRateChangesRequest) -> List[RateChangeDTO]:
        worker = self._worker_repo.find_by_id(request.worker_id)
        if worker is None or worker.project_id != request.project_id:
            raise WorkerNotFoundError(str(request.worker_id))

        changes = self._rate_repo.list_by_worker(request.worker_id)
        return [_to_dto(rc) for rc in changes]
