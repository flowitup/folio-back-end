"""Get monthly labor summary use case.

Aggregates total_days + total_cost per (year, month) across every worker on
a project. Used by the Summary tab to render a year-grouped breakdown when
no specific month is selected.
"""

from dataclasses import dataclass
from typing import List
from uuid import UUID

from app.application.labor.ports import ILaborEntryRepository


@dataclass
class MonthlySummaryRow:
    year: int
    month: int
    total_days: int
    total_cost: float


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
                    total_days=r.total_days,
                    total_cost=float(r.total_cost),
                )
                for r in rows
            ],
        )
