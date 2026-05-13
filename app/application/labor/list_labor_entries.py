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
    shift_type: Optional[str]
    supplement_hours: int
    created_at: str
    # Optional with a default so existing test fixtures and any legacy
    # callsite that constructed LaborEntryDetail before avatars existed
    # continue to work unchanged.
    worker_avatar_url: Optional[str] = None


@dataclass
class ListLaborEntriesRequest:
    project_id: UUID
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    worker_id: Optional[UUID] = None
    # Cap returned rows to the most recent N. The route enforces a default
    # (500) and an upper bound (1000) — keep this Optional so callers that
    # genuinely want everything (e.g. exports) can pass None.
    limit: Optional[int] = None


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
            limit=request.limit,
        )

        result = []
        for entry in entries:
            worker = worker_map.get(entry.worker_id)
            if not worker:
                continue  # Skip orphaned entries

            # Delegate effective cost to the domain entity
            effective_cost = float(entry.effective_cost(worker.daily_rate))

            result.append(
                LaborEntryDetail(
                    id=str(entry.id),
                    worker_id=str(entry.worker_id),
                    worker_name=worker.name,
                    worker_avatar_url=worker.avatar_url,
                    date=entry.date.isoformat(),
                    amount_override=float(entry.amount_override) if entry.amount_override else None,
                    effective_cost=effective_cost,
                    note=entry.note,
                    shift_type=entry.shift_type,
                    supplement_hours=entry.supplement_hours,
                    created_at=entry.created_at.isoformat(),
                )
            )

        return result
