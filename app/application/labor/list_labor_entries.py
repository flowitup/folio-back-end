"""List labor entries use case."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from app.application.labor.ports import IWorkerRepository, ILaborEntryRepository, IWorkerRateChangeRepository
from app.domain.entities.worker import Worker
from app.domain.entities.worker_rate_change import WorkerRateChange


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
    role_color: Optional[str] = None
    status: str = "validated"
    submitted_by_user_id: Optional[str] = None
    validated_by_user_id: Optional[str] = None
    validated_at: Optional[str] = None
    # Open worker change request on a validated day (None when none).
    change_requested_at: Optional[str] = None
    proposed_shift_type: Optional[str] = None
    proposed_supplement_hours: Optional[int] = None
    proposed_note: Optional[str] = None


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
    # Validation status filter ("pending" | "validated"); None = every status.
    status: Optional[str] = None


def _resolve_rate(worker: Worker, entry_date: date, rate_changes: List[WorkerRateChange]) -> Decimal:
    """Return the effective daily rate for worker on entry_date.

    ``rate_changes`` must be ordered effective_date DESC (as returned by the
    repository).  The first change whose effective_date <= entry_date is the
    winner; if no change qualifies, fall back to worker.daily_rate.
    """
    for rc in rate_changes:
        if rc.effective_date <= entry_date:
            return rc.daily_rate
    return worker.daily_rate


class ListLaborEntriesUseCase:
    """List labor entries for a project with optional filters.

    Injects IWorkerRateChangeRepository to resolve the effective daily rate per
    entry date rather than using the single current worker.daily_rate.  This
    prevents retroactive repricing when the worker's base rate is edited.
    """

    def __init__(
        self,
        worker_repo: IWorkerRepository,
        entry_repo: ILaborEntryRepository,
        rate_change_repo: Optional[IWorkerRateChangeRepository] = None,
    ):
        self._worker_repo = worker_repo
        self._entry_repo = entry_repo
        self._rate_repo = rate_change_repo

    def execute(self, request: ListLaborEntriesRequest) -> List[LaborEntryDetail]:
        # Get all workers for lookup (including inactive for historical entries)
        workers = self._worker_repo.list_by_project(request.project_id, active_only=False)
        worker_map = {w.id: w for w in workers}

        # Fetch rate timelines for all workers in one query; fall back to empty
        # dict when no rate-change repo is wired (backward-compat path).
        rate_map: Dict[UUID, List[WorkerRateChange]] = {}
        if self._rate_repo is not None and worker_map:
            rate_map = self._rate_repo.list_by_workers(list(worker_map.keys()))

        entries = self._entry_repo.list_by_project(
            project_id=request.project_id,
            date_from=request.date_from,
            date_to=request.date_to,
            worker_id=request.worker_id,
            limit=request.limit,
            status=request.status,
        )

        result = []
        for entry in entries:
            worker = worker_map.get(entry.worker_id)
            if not worker:
                continue  # Skip orphaned entries

            # Resolve effective rate for this entry's date, then delegate cost
            # computation to the domain entity (amount_override still wins there).
            resolved_rate = _resolve_rate(worker, entry.date, rate_map.get(entry.worker_id, []))
            # Pending (worker-submitted, not yet validated) rows are unpriced everywhere:
            # the web table, calendar cells and the mobile panel sum this field client-side,
            # so it must be 0 until a manager validates — matching the server-side summaries.
            effective_cost = 0.0 if entry.is_pending else float(entry.effective_cost(resolved_rate))

            result.append(
                LaborEntryDetail(
                    id=str(entry.id),
                    worker_id=str(entry.worker_id),
                    worker_name=worker.person_name or worker.name,
                    date=entry.date.isoformat(),
                    amount_override=float(entry.amount_override) if entry.amount_override else None,
                    effective_cost=effective_cost,
                    note=entry.note,
                    shift_type=entry.shift_type,
                    supplement_hours=entry.supplement_hours,
                    created_at=entry.created_at.isoformat(),
                    role_color=worker.role_color,
                    status=entry.status,
                    submitted_by_user_id=str(entry.submitted_by_user_id) if entry.submitted_by_user_id else None,
                    validated_by_user_id=str(entry.validated_by_user_id) if entry.validated_by_user_id else None,
                    validated_at=entry.validated_at.isoformat() if entry.validated_at else None,
                    change_requested_at=entry.change_requested_at.isoformat() if entry.change_requested_at else None,
                    proposed_shift_type=entry.proposed_shift_type,
                    proposed_supplement_hours=entry.proposed_supplement_hours,
                    proposed_note=entry.proposed_note,
                )
            )

        return result
