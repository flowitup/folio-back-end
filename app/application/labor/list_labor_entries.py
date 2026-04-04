"""List labor entries use case."""

from dataclasses import dataclass
from datetime import date
from typing import List, Optional
from uuid import UUID

from app.application.labor.ports import IWorkerRepository, ILaborEntryRepository


@dataclass
class LaborEntryDetail:
    id: str
    worker_id: str
    worker_name: str
    date: str
    amount_override: Optional[float]
    effective_cost: float
    note: Optional[str]
    created_at: str


@dataclass
class ListLaborEntriesRequest:
    project_id: UUID
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    worker_id: Optional[UUID] = None


class ListLaborEntriesUseCase:
    """List labor entries for a project with optional filters."""

    def __init__(
        self,
        worker_repo: IWorkerRepository,
        entry_repo: ILaborEntryRepository,
    ):
        self._worker_repo = worker_repo
        self._entry_repo = entry_repo

    def execute(self, request: ListLaborEntriesRequest) -> List[LaborEntryDetail]:
        # Get all workers for lookup (including inactive for historical entries)
        workers = self._worker_repo.list_by_project(request.project_id, active_only=False)
        worker_map = {w.id: w for w in workers}

        entries = self._entry_repo.list_by_project(
            project_id=request.project_id,
            date_from=request.date_from,
            date_to=request.date_to,
            worker_id=request.worker_id,
        )

        result = []
        for entry in entries:
            worker = worker_map.get(entry.worker_id)
            if not worker:
                continue  # Skip orphaned entries

            # Effective cost: override if set, else daily rate
            effective_cost = (
                float(entry.amount_override)
                if entry.amount_override is not None
                else float(worker.daily_rate)
            )

            result.append(
                LaborEntryDetail(
                    id=str(entry.id),
                    worker_id=str(entry.worker_id),
                    worker_name=worker.name,
                    date=entry.date.isoformat(),
                    amount_override=float(entry.amount_override) if entry.amount_override else None,
                    effective_cost=effective_cost,
                    note=entry.note,
                    created_at=entry.created_at.isoformat(),
                )
            )

        return result
