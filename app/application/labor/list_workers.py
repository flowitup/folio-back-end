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
    # Joined Person identity (cook 1d-ii-a). Optional during the Phase 1c
    # backfill rollout. Mirrors WorkerResponse so the API serializer can
    # accept either a Worker entity or a WorkerSummary uniformly.
    person_id: Optional[str] = None
    person_name: Optional[str] = None
    person_phone: Optional[str] = None
    # Joined LaborRole identity. None for workers without a role assignment.
    role_id: Optional[str] = None
    role_name: Optional[str] = None
    role_color: Optional[str] = None


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
                person_id=str(w.person_id) if w.person_id else None,
                person_name=w.person_name,
                person_phone=w.person_phone,
                role_id=str(w.role_id) if w.role_id else None,
                role_name=w.role_name,
                role_color=w.role_color,
            )
            for w in workers
        ]
