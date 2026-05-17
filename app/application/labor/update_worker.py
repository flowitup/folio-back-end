"""Update worker use case."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.application.labor.ports import IWorkerRepository
from app.domain.exceptions.labor_exceptions import (
    WorkerNotFoundError,
    InvalidWorkerDataError,
)


_ROLE_SENTINEL = object()


@dataclass
class UpdateWorkerRequest:
    worker_id: UUID
    name: Optional[str] = None
    phone: Optional[str] = None
    daily_rate: Optional[Decimal] = None
    # Use a sentinel so callers can explicitly clear the role assignment
    # (role_id=None means "clear"; omit the field to leave unchanged).
    role_id: object = _ROLE_SENTINEL


@dataclass
class UpdateWorkerResponse:
    id: str
    project_id: str
    name: str
    phone: Optional[str]
    daily_rate: float
    is_active: bool
    created_at: str
    # Joined Person identity (cook 1d-ii-a).
    person_id: Optional[str] = None
    person_name: Optional[str] = None
    person_phone: Optional[str] = None
    # Joined LaborRole identity.
    role_id: Optional[str] = None
    role_name: Optional[str] = None
    role_color: Optional[str] = None


class UpdateWorkerUseCase:
    """Update an existing worker."""

    def __init__(self, worker_repo: IWorkerRepository):
        self._repo = worker_repo

    def execute(self, request: UpdateWorkerRequest) -> UpdateWorkerResponse:
        worker = self._repo.find_by_id(request.worker_id)
        if not worker:
            raise WorkerNotFoundError(str(request.worker_id))

        if request.name is not None:
            if len(request.name.strip()) == 0:
                raise InvalidWorkerDataError("Worker name cannot be empty")
            if len(request.name) > 255:
                raise InvalidWorkerDataError("Worker name exceeds 255 characters")
            worker.name = request.name.strip()

        if request.phone is not None:
            worker.phone = request.phone.strip() if request.phone else None

        if request.daily_rate is not None:
            if request.daily_rate <= 0:
                raise InvalidWorkerDataError("Daily rate must be greater than 0")
            worker.daily_rate = request.daily_rate

        if request.role_id is not _ROLE_SENTINEL:
            worker.role_id = request.role_id  # type: ignore[assignment]

        worker.updated_at = datetime.now(timezone.utc)
        saved = self._repo.update(worker)

        return UpdateWorkerResponse(
            id=str(saved.id),
            project_id=str(saved.project_id),
            name=saved.name,
            phone=saved.phone,
            daily_rate=float(saved.daily_rate),
            is_active=saved.is_active,
            created_at=saved.created_at.isoformat(),
            person_id=str(saved.person_id) if saved.person_id else None,
            person_name=saved.person_name,
            person_phone=saved.person_phone,
            role_id=str(saved.role_id) if saved.role_id else None,
            role_name=saved.role_name,
            role_color=saved.role_color,
        )
