"""Get labor payments summary use case.

Aggregates paid amounts per (service_month, worker) across a project's
labor-type invoices, for the Labor Payments Hub summary view. Mirrors the
shape of GetMonthlyLaborSummaryUseCase (per-month buckets with nested
per-worker sub-rows) but sources amounts from settled labor invoices instead
of logged attendance.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from app.application.invoice.dtos import money
from app.application.invoice.ports import IInvoiceRepository


@dataclass
class LaborPaymentsWorkerResponse:
    """One worker's paid total within a month bucket, quantized to cents."""

    worker_id: str
    worker_name: str
    paid: float
    invoice_count: int


@dataclass
class LaborPaymentsMonthResponse:
    """One (service_month) bucket of the labor-payments summary response.

    total_paid = sum of workers' paid + unassigned_paid for this bucket —
    every labor invoice in the bucket, whether linked to a worker or not.
    """

    year: Optional[int]
    month: Optional[int]
    total_paid: float
    workers: List[LaborPaymentsWorkerResponse] = field(default_factory=list)
    unassigned_paid: float = 0.0
    unassigned_count: int = 0
    # Flagged-method split of the bucket's paid total (company/personal
    # payment-method flags). No-method or unflagged invoices count in
    # neither, so company_paid + personal_paid <= total_paid.
    company_paid: float = 0.0
    personal_paid: float = 0.0


@dataclass
class LaborPaymentsSummaryResponse:
    months: List[LaborPaymentsMonthResponse]


@dataclass
class GetLaborPaymentsSummaryRequest:
    project_id: UUID


class GetLaborPaymentsSummaryUseCase:
    """Return per-(service_month, worker) paid totals for a project's labor invoices."""

    def __init__(self, invoice_repo: IInvoiceRepository) -> None:
        self._repo = invoice_repo

    def execute(self, request: GetLaborPaymentsSummaryRequest) -> LaborPaymentsSummaryResponse:
        rows = self._repo.get_labor_payments_summary(project_id=request.project_id)

        months = []
        for r in rows:
            workers_total = sum((w.paid for w in r.workers), Decimal("0"))
            months.append(
                LaborPaymentsMonthResponse(
                    year=r.year,
                    month=r.month,
                    total_paid=money(workers_total + r.unassigned_paid),
                    workers=[
                        LaborPaymentsWorkerResponse(
                            worker_id=str(w.worker_id),
                            worker_name=w.worker_name,
                            paid=money(w.paid),
                            invoice_count=w.invoice_count,
                        )
                        for w in r.workers
                    ],
                    unassigned_paid=money(r.unassigned_paid),
                    unassigned_count=r.unassigned_count,
                    company_paid=money(r.company_paid),
                    personal_paid=money(r.personal_paid),
                )
            )
        return LaborPaymentsSummaryResponse(months=months)
