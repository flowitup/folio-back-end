"""Unit tests for GetLaborPaymentsSummaryUseCase.

Covers DTO mapping / money quantization from the repo's port-level dataclasses.
Aggregation itself (grouping, ordering, unassigned bucketing) is covered at the
repository level in tests/unit/infrastructure/test_labor_payments_summary_repo.py.
"""

from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from app.application.invoice.get_labor_payments_summary_usecase import (
    GetLaborPaymentsSummaryRequest,
    GetLaborPaymentsSummaryUseCase,
)
from app.application.invoice.ports import IInvoiceRepository, LaborPaymentsMonthRow, LaborPaymentsWorkerRow


def test_empty_project_returns_empty_months():
    repo = MagicMock(spec=IInvoiceRepository)
    repo.get_labor_payments_summary.return_value = []

    use_case = GetLaborPaymentsSummaryUseCase(repo)
    project_id = uuid4()
    result = use_case.execute(GetLaborPaymentsSummaryRequest(project_id=project_id))

    assert result.months == []
    repo.get_labor_payments_summary.assert_called_once_with(project_id=project_id)


def test_total_paid_sums_workers_and_unassigned():
    repo = MagicMock(spec=IInvoiceRepository)
    worker_id = uuid4()
    repo.get_labor_payments_summary.return_value = [
        LaborPaymentsMonthRow(
            year=2026,
            month=7,
            workers=[
                LaborPaymentsWorkerRow(
                    worker_id=worker_id, worker_name="Jean Dupont", paid=Decimal("1000.005"), invoice_count=2
                )
            ],
            unassigned_paid=Decimal("234.561"),
            unassigned_count=1,
        )
    ]

    use_case = GetLaborPaymentsSummaryUseCase(repo)
    result = use_case.execute(GetLaborPaymentsSummaryRequest(project_id=uuid4()))

    assert len(result.months) == 1
    month = result.months[0]
    assert month.year == 2026
    assert month.month == 7
    # Money is quantized 2dp HALF_UP at the DTO boundary.
    assert month.workers[0].paid == 1000.01
    assert month.unassigned_paid == 234.56
    # total_paid sums the RAW (pre-quantization) components, then quantizes once —
    # not the sum of the already-rounded per-row figures.
    assert month.total_paid == 1234.57
    assert month.workers[0].worker_id == str(worker_id)
    assert month.workers[0].worker_name == "Jean Dupont"
    assert month.workers[0].invoice_count == 2
    assert month.unassigned_count == 1


def test_no_month_bucket_maps_year_month_to_none():
    repo = MagicMock(spec=IInvoiceRepository)
    repo.get_labor_payments_summary.return_value = [
        LaborPaymentsMonthRow(year=None, month=None, workers=[], unassigned_paid=Decimal("50"), unassigned_count=1),
    ]

    use_case = GetLaborPaymentsSummaryUseCase(repo)
    result = use_case.execute(GetLaborPaymentsSummaryRequest(project_id=uuid4()))

    assert result.months[0].year is None
    assert result.months[0].month is None
    assert result.months[0].total_paid == 50.0


def test_preserves_bucket_order_from_repo():
    """The use-case must not re-sort — ordering is the repo's responsibility."""
    repo = MagicMock(spec=IInvoiceRepository)
    repo.get_labor_payments_summary.return_value = [
        LaborPaymentsMonthRow(year=2026, month=7, workers=[], unassigned_paid=Decimal("0"), unassigned_count=0),
        LaborPaymentsMonthRow(year=2026, month=6, workers=[], unassigned_paid=Decimal("0"), unassigned_count=0),
        LaborPaymentsMonthRow(year=None, month=None, workers=[], unassigned_paid=Decimal("0"), unassigned_count=0),
    ]

    use_case = GetLaborPaymentsSummaryUseCase(repo)
    result = use_case.execute(GetLaborPaymentsSummaryRequest(project_id=uuid4()))

    assert [(m.year, m.month) for m in result.months] == [(2026, 7), (2026, 6), (None, None)]
