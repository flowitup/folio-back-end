"""Get labor summary use case."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from app.application.labor.ports import ILaborEntryRepository
from app.domain.labor.banked_hours_bonus import bonus_for_banked_hours


@dataclass
class WorkerCostSummary:
    worker_id: str
    worker_name: str
    # Fractional — see ports.LaborSummaryRow.days_worked. A mix of
    # full + half shifts cleanly produces 2.5, not 3.
    days_worked: float
    total_cost: float
    banked_hours: int
    bonus_full_days: int
    bonus_half_days: int
    bonus_cost: float


@dataclass
class LaborSummaryResponse:
    rows: List[WorkerCostSummary]
    # Sum of per-worker days_worked — also fractional.
    total_days: float
    total_cost: float
    total_banked_hours: int
    total_bonus_days: float
    total_bonus_cost: float


@dataclass
class GetLaborSummaryRequest:
    project_id: UUID
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    worker_id: Optional[UUID] = None


class GetLaborSummaryUseCase:
    """Get aggregated labor summary for a project."""

    def __init__(self, entry_repo: ILaborEntryRepository):
        self._repo = entry_repo

    def execute(self, request: GetLaborSummaryRequest) -> LaborSummaryResponse:
        summary_rows = self._repo.get_summary(
            project_id=request.project_id,
            date_from=request.date_from,
            date_to=request.date_to,
            worker_id=request.worker_id,
        )

        rows: List[WorkerCostSummary] = []
        total_banked_hours = 0
        total_bonus_days = Decimal("0")
        total_bonus_cost = Decimal("0")

        for row in summary_rows:
            banked = row.banked_hours or 0
            bonus = bonus_for_banked_hours(banked, row.daily_rate)
            priced_cost = row.total_cost  # already Decimal from repo

            rows.append(
                WorkerCostSummary(
                    worker_id=str(row.worker_id),
                    worker_name=row.worker_name,
                    days_worked=float(row.days_worked),
                    total_cost=float(priced_cost + bonus.cost),
                    banked_hours=banked,
                    bonus_full_days=bonus.full_days,
                    bonus_half_days=bonus.half_days,
                    bonus_cost=float(bonus.cost),
                )
            )

            total_banked_hours += banked
            total_bonus_days += bonus.days
            total_bonus_cost += bonus.cost

        total_days = float(sum(r.days_worked for r in rows))
        total_cost = sum(r.total_cost for r in rows)

        return LaborSummaryResponse(
            rows=rows,
            total_days=total_days,
            total_cost=total_cost,
            total_banked_hours=total_banked_hours,
            total_bonus_days=float(total_bonus_days),
            total_bonus_cost=float(total_bonus_cost),
        )
