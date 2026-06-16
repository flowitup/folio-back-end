"""SetWorkerRateChange use case.

Creates or updates (upserts) an effective-dated daily-rate change for a worker.
The project_id guard prevents cross-project edits even when the caller holds
a valid worker UUID from another project.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from app.application.labor.ports import IWorkerRepository, IWorkerRateChangeRepository
from app.domain.entities.worker_rate_change import WorkerRateChange
from app.domain.exceptions.labor_exceptions import InvalidRateChangeError, WorkerNotFoundError


@dataclass
class SetWorkerRateChangeRequest:
    project_id: UUID
    worker_id: UUID
    effective_date: date
    daily_rate: Decimal


@dataclass
class RateChangeDTO:
    id: str
    worker_id: str
    effective_date: str  # ISO date
    daily_rate: float
    created_at: str  # ISO datetime


class SetWorkerRateChangeUseCase:
    """Upsert an effective-dated rate for a worker.

    Raises:
        WorkerNotFoundError: worker does not exist or belongs to a different project.
        InvalidRateChangeError: daily_rate is <= 0.
    """

    def __init__(
        self,
        worker_repo: IWorkerRepository,
        rate_change_repo: IWorkerRateChangeRepository,
    ) -> None:
        self._worker_repo = worker_repo
        self._rate_repo = rate_change_repo

    def execute(self, request: SetWorkerRateChangeRequest) -> RateChangeDTO:
        worker = self._worker_repo.find_by_id(request.worker_id)
        if worker is None or worker.project_id != request.project_id:
            raise WorkerNotFoundError(str(request.worker_id))

        if request.daily_rate is None or request.daily_rate <= 0:
            raise InvalidRateChangeError("daily_rate must be > 0")

        entity = WorkerRateChange(
            id=uuid4(),
            worker_id=request.worker_id,
            effective_date=request.effective_date,
            daily_rate=Decimal(str(request.daily_rate)),
            created_at=datetime.now(timezone.utc),
        )
        saved = self._rate_repo.upsert(entity)
        return _to_dto(saved)


def _to_dto(rc: WorkerRateChange) -> RateChangeDTO:
    return RateChangeDTO(
        id=str(rc.id),
        worker_id=str(rc.worker_id),
        effective_date=rc.effective_date.isoformat(),
        daily_rate=float(rc.daily_rate),
        created_at=rc.created_at.isoformat(),
    )
