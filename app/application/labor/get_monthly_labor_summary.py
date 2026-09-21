"""Get monthly labor summary use case.

Aggregates total_days + total_cost per (year, month) across every worker on
a project, with the per-worker breakdown nested inline. Used by the Summary
tab to render a year-grouped breakdown when no specific month is selected.

Earned money here means the same thing as on the per-worker summary: priced
attendance plus the banked-hours bonus. Leaving the bonus out showed a worker
two different totals for one month depending on which screen they opened.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List
from uuid import UUID

from app.application.labor.ports import ILaborEntryRepository
from app.domain.labor.banked_hours_bonus import bonus_for_banked_hours


@dataclass
class MonthlyWorkerSubRow:
    """One worker's contribution within a (year, month) bucket."""

    worker_id: str
    worker_name: str
    # Fractional priced days — see ports.MonthlyWorkerSubRow.
    days_worked: float
    # Priced attendance + bonus_cost.
    total_cost: float
    # Share of total_cost earned by converting banked hours into paid days.
    bonus_cost: float


@dataclass
class MonthlySummaryRow:
    year: int
    month: int
    # Fractional priced days (sum of per-worker days_worked). Bonus days are not
    # attendance, so they raise the cost without raising this count.
    total_days: float
    total_cost: float
    total_bonus_cost: float
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
        rows: List[MonthlySummaryRow] = []

        for r in self._repo.get_monthly_summary(project_id=request.project_id):
            workers: List[MonthlyWorkerSubRow] = []
            month_bonus_cost = Decimal("0")

            for w in r.workers:
                bonus = bonus_for_banked_hours(w.banked_hours, w.daily_rate)
                month_bonus_cost += bonus.cost
                workers.append(
                    MonthlyWorkerSubRow(
                        worker_id=str(w.worker_id),
                        worker_name=w.worker_name,
                        days_worked=float(w.days_worked),
                        total_cost=float(w.total_cost + bonus.cost),
                        bonus_cost=float(bonus.cost),
                    )
                )

            rows.append(
                MonthlySummaryRow(
                    year=r.year,
                    month=r.month,
                    total_days=float(r.total_days),
                    total_cost=float(r.total_cost + month_bonus_cost),
                    total_bonus_cost=float(month_bonus_cost),
                    workers=workers,
                )
            )

        return LaborMonthlySummaryResponse(rows=rows)
