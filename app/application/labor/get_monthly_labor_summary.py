"""Get monthly labor summary use case.

Aggregates total_days + total_cost per (year, month) across every worker on
a project, with the per-worker breakdown nested inline. Used by the Summary
tab to render a year-grouped breakdown when no specific month is selected.
"""

from dataclasses import dataclass
from typing import List
from uuid import UUID

from app.application.labor.ports import ILaborEntryRepository


@dataclass
class MonthlyWorkerSubRow:
    """One worker's contribution within a (year, month) bucket."""

    worker_id: str
    worker_name: str
    # Fractional priced days — see ports.MonthlyWorkerSubRow.
    days_worked: float
    total_cost: float


@dataclass
class MonthlySummaryRow:
    year: int
    month: int
    # Fractional priced days (sum of per-worker days_worked).
    total_days: float
    total_cost: float
    workers: List[MonthlyWorkerSubRow]


@dataclass
class LaborMonthlySummaryResponse:
    rows: List[MonthlySummaryRow]


@dataclass
class GetMonthlyLaborSummaryRequest:
    project_id: UUID


class GetMonthlyLaborSummaryUseCase:
    """Return per-month labor totals for a project, ordered most-recent first."""

    def __init__(self, entry_repo: ILaborEntryRepository):
        self._repo = entry_repo

    def execute(self, request: GetMonthlyLaborSummaryRequest) -> LaborMonthlySummaryResponse:
        rows = self._repo.get_monthly_summary(project_id=request.project_id)
        return LaborMonthlySummaryResponse(
            rows=[
                MonthlySummaryRow(
                    year=r.year,
                    month=r.month,
                    total_days=float(r.total_days),
                    total_cost=float(r.total_cost),
                    workers=[
                        MonthlyWorkerSubRow(
                            worker_id=str(w.worker_id),
                            worker_name=w.worker_name,
                            days_worked=float(w.days_worked),
                            total_cost=float(w.total_cost),
                        )
                        for w in r.workers
                    ],
                )
                for r in rows
            ],
        )
