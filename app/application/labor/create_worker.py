"""Create worker use case."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from app.application.labor.ports import IWorkerRepository
from app.domain.entities.worker import Worker
from app.domain.exceptions.labor_exceptions import InvalidWorkerDataError


@dataclass
class CreateWorkerRequest:
    project_id: UUID
    name: str
    daily_rate: Decimal
    phone: Optional[str] = None


@dataclass
class CreateWorkerResponse:
    id: str
    project_id: str
    name: str
    phone: Optional[str]
    daily_rate: float
    is_active: bool
    created_at: str


class CreateWorkerUseCase:
    """Create a new worker for a project."""

    def __init__(self, worker_repo: IWorkerRepository):
        self._repo = worker_repo

    def execute(self, request: CreateWorkerRequest) -> CreateWorkerResponse:
        name = request.name.strip() if request.name else ""
        if not name:
            raise InvalidWorkerDataError("Worker name is required")
        if len(name) > 255:
            raise InvalidWorkerDataError("Worker name exceeds 255 characters")
        if request.daily_rate <= 0:
            raise InvalidWorkerDataError("Daily rate must be greater than 0")

        worker = Worker(
            id=uuid4(),
            project_id=request.project_id,
            name=name,
            daily_rate=request.daily_rate,
            phone=request.phone.strip() if request.phone else None,
            created_at=datetime.now(timezone.utc),
        )

        saved = self._repo.create(worker)

        return CreateWorkerResponse(
            id=str(saved.id),
            project_id=str(saved.project_id),
            name=saved.name,
            phone=saved.phone,
            daily_rate=float(saved.daily_rate),
            is_active=saved.is_active,
            created_at=saved.created_at.isoformat(),
        )
