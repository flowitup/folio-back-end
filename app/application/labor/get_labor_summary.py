"""Get labor summary use case."""

from dataclasses import dataclass
from datetime import date
from typing import List, Optional
from uuid import UUID

from app.application.labor.ports import ILaborEntryRepository


@dataclass
class WorkerCostSummary:
    worker_id: str
    worker_name: str
    days_worked: int
    total_cost: float


@dataclass
class LaborSummaryResponse:
    rows: List[WorkerCostSummary]
    total_days: int
    total_cost: float


@dataclass
class GetLaborSummaryRequest:
    project_id: UUID
    date_from: Optional[date] = None
    date_to: Optional[date] = None


class GetLaborSummaryUseCase:
    """Get aggregated labor summary for a project."""

    def __init__(self, entry_repo: ILaborEntryRepository):
        self._repo = entry_repo

    def execute(self, request: GetLaborSummaryRequest) -> LaborSummaryResponse:
        summary_rows = self._repo.get_summary(
            project_id=request.project_id,
            date_from=request.date_from,
            date_to=request.date_to,
        )

        rows = [
            WorkerCostSummary(
                worker_id=str(row.worker_id),
                worker_name=row.worker_name,
                days_worked=row.days_worked,
                total_cost=float(row.total_cost),
            )
            for row in summary_rows
        ]

        total_days = sum(r.days_worked for r in rows)
        total_cost = sum(r.total_cost for r in rows)

        return LaborSummaryResponse(
            rows=rows,
            total_days=total_days,
            total_cost=total_cost,
        )
