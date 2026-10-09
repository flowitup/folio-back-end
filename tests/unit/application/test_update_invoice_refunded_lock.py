"""A refunded expense is locked for edits, except for its cosmetic highlight."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.invoice.ports import IInvoiceRepository
from app.application.invoice.update_invoice import UpdateInvoiceRequest, UpdateInvoiceUseCase
from app.domain.entities.invoice import Invoice, InvoiceType, RefundableStatus
from app.domain.exceptions.invoice_exceptions import InvalidInvoiceDataError
from app.domain.value_objects.invoice_item import InvoiceItem


def _refunded_expense(status: RefundableStatus = RefundableStatus.REFUNDED) -> Invoice:
    return Invoice(
        id=uuid4(),
        project_id=uuid4(),
        invoice_number="INV-2026-0002",
        type=InvoiceType.MATERIALS_SERVICES,
        issue_date=date.today(),
        recipient_name="Supplier Co",
        recipient_address=None,
        notes=None,
        items=[InvoiceItem(description="Work", quantity=Decimal("1"), unit_price=Decimal("100"))],
        created_by=uuid4(),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        refundable_status=status.value,
    )


def _repo(invoice: Invoice) -> MagicMock:
    repo = MagicMock(spec=IInvoiceRepository)
    repo.find_by_id.return_value = invoice
    repo.update.side_effect = lambda inv: inv
    repo.sum_refunds_for_source.return_value = Decimal("0")
    repo.sum_applied_for_target.return_value = Decimal("0")
    return repo


@pytest.mark.parametrize("color", ["yellow", None])
def test_highlight_only_edit_of_a_refunded_expense_is_allowed(color):
    invoice = _refunded_expense()
    repo = _repo(invoice)

    result = UpdateInvoiceUseCase(repo).execute(UpdateInvoiceRequest(invoice_id=invoice.id, highlight_color=color))

    assert result.highlight_color == color
    assert result.refundable_status == RefundableStatus.REFUNDED.value
    assert result.total_amount == 100.0
    repo.update.assert_called_once()


@pytest.mark.parametrize(
    "fields",
    [
        {"notes": "changed"},
        {"highlight_color": "red", "notes": "changed"},
        {"highlight_color": "red", "items": [{"description": "x", "quantity": 1, "unit_price": 1}]},
        {"highlight_color": "red", "payment_method_id": None},
    ],
)
def test_any_other_edit_of_a_refunded_expense_stays_locked(fields):
    invoice = _refunded_expense()
    repo = _repo(invoice)

    with pytest.raises(InvalidInvoiceDataError, match="Refunded expenses are locked"):
        UpdateInvoiceUseCase(repo).execute(UpdateInvoiceRequest(invoice_id=invoice.id, **fields))
    repo.update.assert_not_called()


def test_a_pending_refund_is_not_locked():
    invoice = _refunded_expense(RefundableStatus.REFUND_PENDING)

    result = UpdateInvoiceUseCase(_repo(invoice)).execute(UpdateInvoiceRequest(invoice_id=invoice.id, notes="changed"))

    assert result.notes == "changed"
