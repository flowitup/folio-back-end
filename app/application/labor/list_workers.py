"""List workers use case."""

from dataclasses import dataclass
from typing import List, Optional
from uuid import UUID

from app.application.labor.ports import IWorkerRepository


@dataclass
class WorkerSummary:
    id: str
    project_id: str
    name: str
    phone: Optional[str]
    daily_rate: float
    is_active: bool
    created_at: str


@dataclass
class ListWorkersRequest:
    project_id: UUID


class ListWorkersUseCase:
    """List workers for a project."""

    def __init__(self, worker_repo: IWorkerRepository):
        self._repo = worker_repo

    def execute(self, request: ListWorkersRequest) -> List[WorkerSummary]:
        workers = self._repo.list_by_project(request.project_id, active_only=True)

        return [
            WorkerSummary(
                id=str(w.id),
                project_id=str(w.project_id),
                name=w.name,
                phone=w.phone,
                daily_rate=float(w.daily_rate),
                is_active=w.is_active,
                created_at=w.created_at.isoformat(),
            )
            for w in workers
        ]
