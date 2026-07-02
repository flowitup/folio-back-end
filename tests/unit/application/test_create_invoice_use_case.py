"""Unit tests for CreateInvoiceUseCase."""

from datetime import date, datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, Mock
from uuid import uuid4
import pytest

from app.application.invoice.create_invoice import CreateInvoiceUseCase, CreateInvoiceRequest
from app.application.invoice.delete_invoice import DeleteInvoiceUseCase
from app.application.invoice.ports import IInvoiceRepository
from app.application.invoice.update_invoice import UpdateInvoiceUseCase, UpdateInvoiceRequest
from app.application.tags.exceptions import InvalidProjectTagError
from app.domain.entities.invoice import Invoice, InvoiceType, RefundableStatus
from app.domain.entities.project_tag import ProjectTag
from app.domain.exceptions.invoice_exceptions import InvalidInvoiceDataError, ServiceMonthNotAllowedError


def make_mock_repo():
    """Create mock repository with sensible defaults."""
    repo = MagicMock(spec=IInvoiceRepository)
    repo.next_invoice_number.return_value = "INV-2026-0001"
    repo.create.side_effect = lambda inv: inv  # return as-is
    return repo


def make_request(**kwargs):
    """Factory helper for test requests."""
    defaults = dict(
        project_id=uuid4(),
        created_by=uuid4(),
        type=InvoiceType.RELEASED_FUNDS,
        issue_date=date.today(),
        recipient_name="ACME Corp",
        items=[{"description": "Work", "quantity": 10, "unit_price": 50}],
    )
    defaults.update(kwargs)
    return CreateInvoiceRequest(**defaults)


class TestCreateInvoiceSuccess:
    """Tests for successful invoice creation."""

    def test_create_invoice_success(self):
        """Should create invoice with valid data."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)
        request = make_request()

        result = use_case.execute(request)

        assert result.invoice_number == "INV-2026-0001"
        assert result.total_amount == 500.0
        assert result.type == "released_funds"
        assert result.recipient_name == "ACME Corp"
        repo.create.assert_called_once()

    def test_create_invoice_calls_next_invoice_number(self):
        """Should call repo.next_invoice_number with project_id."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)
        project_id = uuid4()
        request = make_request(project_id=project_id)

        use_case.execute(request)

        repo.next_invoice_number.assert_called_once_with(project_id)

    def test_create_invoice_saves_to_repo(self):
        """Should save invoice to repository."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)
        request = make_request()

        use_case.execute(request)

        repo.create.assert_called_once()
        saved_invoice = repo.create.call_args[0][0]
        assert isinstance(saved_invoice, Invoice)
        assert saved_invoice.invoice_number == "INV-2026-0001"

    def test_create_invoice_with_optional_fields(self):
        """Should preserve optional fields when provided."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)
        request = make_request(recipient_address="123 Main St", notes="Important invoice")

        result = use_case.execute(request)

        assert result.recipient_address == "123 Main St"
        assert result.notes == "Important invoice"

    def test_create_invoice_multiple_items(self):
        """Should handle multiple line items."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)
        request = make_request(
            items=[
                {"description": "Work A", "quantity": 2, "unit_price": 100},
                {"description": "Work B", "quantity": 3, "unit_price": 50},
            ]
        )

        result = use_case.execute(request)

        assert result.total_amount == 350.0  # 2*100 + 3*50
        assert len(result.items) == 2

    def test_create_invoice_with_decimal_prices(self):
        """Should handle decimal prices."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)
        request = make_request(items=[{"description": "Work", "quantity": 2.5, "unit_price": 10.50}])

        result = use_case.execute(request)

        assert result.total_amount == 26.25  # 2.5 * 10.50


class TestCreateInvoiceValidationErrors:
    """Tests for validation errors."""

    def test_empty_recipient_raises(self):
        """Should reject empty recipient name."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)

        with pytest.raises(InvalidInvoiceDataError, match="[Rr]ecipient"):
            use_case.execute(make_request(recipient_name=""))

    def test_whitespace_only_recipient_raises(self):
        """Should reject whitespace-only recipient name."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)

        with pytest.raises(InvalidInvoiceDataError, match="[Rr]ecipient"):
            use_case.execute(make_request(recipient_name="   "))

    def test_none_recipient_raises(self):
        """Should reject None recipient name."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)

        with pytest.raises(InvalidInvoiceDataError, match="[Rr]ecipient"):
            use_case.execute(make_request(recipient_name=None))

    def test_empty_items_raises(self):
        """Should reject invoice with no items."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)

        with pytest.raises(InvalidInvoiceDataError, match="[Ii]tem"):
            use_case.execute(make_request(items=[]))

    def test_zero_quantity_raises(self):
        """Should reject item with zero quantity."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)

        with pytest.raises(InvalidInvoiceDataError, match="[Qq]uantity"):
            use_case.execute(make_request(items=[{"description": "X", "quantity": 0, "unit_price": 10}]))

    def test_negative_quantity_raises(self):
        """Should reject item with negative quantity."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)

        with pytest.raises(InvalidInvoiceDataError, match="[Qq]uantity"):
            use_case.execute(make_request(items=[{"description": "X", "quantity": -1, "unit_price": 10}]))

    def test_negative_price_raises(self):
        """Should reject item with negative price."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)

        with pytest.raises(InvalidInvoiceDataError, match="[Pp]rice|[Uu]nit"):
            use_case.execute(make_request(items=[{"description": "X", "quantity": 1, "unit_price": -5}]))

    def test_empty_description_raises(self):
        """Should reject item with empty description."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)

        with pytest.raises(InvalidInvoiceDataError, match="[Dd]escription"):
            use_case.execute(make_request(items=[{"description": "", "quantity": 1, "unit_price": 10}]))

    def test_whitespace_description_raises(self):
        """Should reject item with whitespace-only description."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)

        with pytest.raises(InvalidInvoiceDataError, match="[Dd]escription"):
            use_case.execute(make_request(items=[{"description": "   ", "quantity": 1, "unit_price": 10}]))

    def test_zero_price_allowed(self):
        """Should allow zero price (e.g., for donation/no-charge items)."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)
        request = make_request(items=[{"description": "X", "quantity": 1, "unit_price": 0}])

        result = use_case.execute(request)

        assert result.total_amount == 0.0


class TestCreateInvoiceInvoiceType:
    """Tests for different invoice types."""

    def test_create_client_invoice(self):
        """Should create client invoice type."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)
        request = make_request(type=InvoiceType.RELEASED_FUNDS)

        result = use_case.execute(request)

        assert result.type == "released_funds"

    def test_create_labor_invoice(self):
        """Should create labor invoice type."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)
        request = make_request(type=InvoiceType.LABOR)

        result = use_case.execute(request)

        assert result.type == "labor"

    def test_create_supplier_invoice(self):
        """Should create supplier invoice type."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)
        request = make_request(type=InvoiceType.MATERIALS_SERVICES)

        result = use_case.execute(request)

        assert result.type == "materials_services"


# ---------------------------------------------------------------------------
# Same-project tag enforcement — CreateInvoice / UpdateInvoice
# ---------------------------------------------------------------------------


def _make_tag(project_id) -> ProjectTag:
    return ProjectTag(
        id=uuid4(),
        project_id=project_id,
        name="Phase A",
        color="#aabbcc",
        created_at=datetime.now(timezone.utc),
    )


def _make_tag_repo(tag):
    repo = Mock()
    repo.get_by_id.return_value = tag
    return repo


def _make_invoice(project_id, **overrides) -> Invoice:
    """Build a minimal Invoice entity for update tests."""
    from app.domain.value_objects.invoice_item import InvoiceItem
    from decimal import Decimal

    defaults = dict(
        id=uuid4(),
        project_id=project_id,
        invoice_number="INV-2026-0001",
        type=InvoiceType.RELEASED_FUNDS,
        issue_date=date.today(),
        recipient_name="ACME Corp",
        recipient_address=None,
        notes=None,
        items=[InvoiceItem(description="Work", quantity=Decimal("1"), unit_price=Decimal("100"))],
        created_by=uuid4(),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        is_auto_generated=False,
    )
    defaults.update(overrides)
    return Invoice(**defaults)


class TestCreateInvoiceTagGuard:
    """CreateInvoiceUseCase must reject tags that belong to a different project."""

    def test_create_invoice_same_project_tag_succeeds(self):
        project_id = uuid4()
        tag = _make_tag(project_id)

        repo = make_mock_repo()
        tag_repo = _make_tag_repo(tag)
        use_case = CreateInvoiceUseCase(repo, tag_repo=tag_repo)

        result = use_case.execute(make_request(project_id=project_id, tag_id=tag.id))

        assert result is not None
        tag_repo.get_by_id.assert_called_once_with(tag.id)

    def test_create_invoice_cross_project_tag_raises(self):
        project_id = uuid4()
        other_project_id = uuid4()
        tag = _make_tag(other_project_id)  # belongs to a DIFFERENT project

        repo = make_mock_repo()
        tag_repo = _make_tag_repo(tag)
        use_case = CreateInvoiceUseCase(repo, tag_repo=tag_repo)

        with pytest.raises(InvalidProjectTagError):
            use_case.execute(make_request(project_id=project_id, tag_id=tag.id))

    def test_create_invoice_nonexistent_tag_raises(self):
        project_id = uuid4()

        repo = make_mock_repo()
        tag_repo = Mock()
        tag_repo.get_by_id.return_value = None  # tag does not exist
        use_case = CreateInvoiceUseCase(repo, tag_repo=tag_repo)

        with pytest.raises(InvalidProjectTagError):
            use_case.execute(make_request(project_id=project_id, tag_id=uuid4()))

    def test_create_invoice_no_tag_skips_guard(self):
        """tag_id=None must not trigger the tag guard."""
        repo = make_mock_repo()
        tag_repo = Mock()
        use_case = CreateInvoiceUseCase(repo, tag_repo=tag_repo)

        result = use_case.execute(make_request(tag_id=None))

        tag_repo.get_by_id.assert_not_called()
        assert result is not None


class TestUpdateInvoiceTagGuard:
    """UpdateInvoiceUseCase must reject tags that belong to a different project."""

    def test_update_invoice_same_project_tag_succeeds(self):
        project_id = uuid4()
        invoice = _make_invoice(project_id)
        tag = _make_tag(project_id)

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        inv_repo.update.side_effect = lambda inv: inv
        tag_repo = _make_tag_repo(tag)
        use_case = UpdateInvoiceUseCase(inv_repo, tag_repo=tag_repo)

        result = use_case.execute(UpdateInvoiceRequest(invoice_id=invoice.id, tag_id=tag.id))

        assert result is not None
        tag_repo.get_by_id.assert_called_once_with(tag.id)

    def test_update_invoice_cross_project_tag_raises(self):
        project_id = uuid4()
        other_project_id = uuid4()
        invoice = _make_invoice(project_id)
        tag = _make_tag(other_project_id)  # belongs to a DIFFERENT project

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        tag_repo = _make_tag_repo(tag)
        use_case = UpdateInvoiceUseCase(inv_repo, tag_repo=tag_repo)

        with pytest.raises(InvalidProjectTagError):
            use_case.execute(UpdateInvoiceRequest(invoice_id=invoice.id, tag_id=tag.id))

    def test_update_invoice_clear_tag_skips_guard(self):
        """tag_id=None (explicit clear) must not trigger the tag guard."""
        project_id = uuid4()
        invoice = _make_invoice(project_id)

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        inv_repo.update.side_effect = lambda inv: inv
        tag_repo = Mock()
        use_case = UpdateInvoiceUseCase(inv_repo, tag_repo=tag_repo)

        result = use_case.execute(UpdateInvoiceRequest(invoice_id=invoice.id, tag_id=None))

        tag_repo.get_by_id.assert_not_called()
        assert result is not None

    def test_update_invoice_unset_tag_skips_guard(self):
        """tag_id not provided (_UNSET sentinel) must not trigger the tag guard."""
        project_id = uuid4()
        invoice = _make_invoice(project_id)

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        inv_repo.update.side_effect = lambda inv: inv
        tag_repo = Mock()
        use_case = UpdateInvoiceUseCase(inv_repo, tag_repo=tag_repo)

        result = use_case.execute(UpdateInvoiceRequest(invoice_id=invoice.id))  # tag_id defaults to _UNSET

        tag_repo.get_by_id.assert_not_called()
        assert result is not None


class TestUpdateInvoiceType:
    """UpdateInvoiceUseCase must persist a changed invoice type."""

    def test_update_invoice_changes_type(self):
        """A provided type must be applied to the saved invoice."""
        project_id = uuid4()
        invoice = _make_invoice(project_id)  # starts as RELEASED_FUNDS

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        inv_repo.update.side_effect = lambda inv: inv
        use_case = UpdateInvoiceUseCase(inv_repo)

        result = use_case.execute(UpdateInvoiceRequest(invoice_id=invoice.id, type=InvoiceType.LABOR))

        assert result.type == "labor"
        saved = inv_repo.update.call_args[0][0]
        assert saved.type == InvoiceType.LABOR

    def test_update_invoice_omitting_type_keeps_original(self):
        """type defaults to None (not provided) and must leave the existing type intact."""
        project_id = uuid4()
        invoice = _make_invoice(project_id)  # RELEASED_FUNDS

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        inv_repo.update.side_effect = lambda inv: inv
        use_case = UpdateInvoiceUseCase(inv_repo)

        result = use_case.execute(UpdateInvoiceRequest(invoice_id=invoice.id, recipient_name="New Name"))

        assert result.type == "released_funds"


class TestUpdateInvoiceVatRate:
    """Regression: updating items must preserve vat_rate (update use-case must not silently drop it)."""

    def test_update_items_with_vat_rate_persisted(self):
        """PATCH items=[{..., vat_rate: 20}] must result in InvoiceItem with vat_rate=20."""
        project_id = uuid4()
        invoice = _make_invoice(project_id)

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        inv_repo.update.side_effect = lambda inv: inv
        use_case = UpdateInvoiceUseCase(inv_repo)

        result = use_case.execute(
            UpdateInvoiceRequest(
                invoice_id=invoice.id,
                items=[{"description": "Service", "quantity": 1, "unit_price": 100, "vat_rate": 20}],
            )
        )

        assert len(result.items) == 1
        item = result.items[0]
        assert item.vat_rate == 20.0
        # TTC = 100 × 1.20 = 120
        assert item.total == 120.0

    def test_update_items_without_vat_rate_defaults_to_zero(self):
        """Items dict without vat_rate key must default to 0 (backward compat)."""
        project_id = uuid4()
        invoice = _make_invoice(project_id)

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        inv_repo.update.side_effect = lambda inv: inv
        use_case = UpdateInvoiceUseCase(inv_repo)

        result = use_case.execute(
            UpdateInvoiceRequest(
                invoice_id=invoice.id,
                items=[{"description": "Work", "quantity": 2, "unit_price": 50}],
            )
        )

        assert result.items[0].vat_rate == 0.0
        assert result.items[0].total == 100.0


def _make_ms_invoice(refundable_status: Optional[str] = None) -> Invoice:
    """Build a materials_services Invoice entity for refund-lock tests."""
    from app.domain.value_objects.invoice_item import InvoiceItem
    from decimal import Decimal

    return Invoice(
        id=uuid4(),
        project_id=uuid4(),
        invoice_number="INV-2026-0099",
        type=InvoiceType.MATERIALS_SERVICES,
        issue_date=date.today(),
        recipient_name="Supplier Co",
        recipient_address=None,
        notes=None,
        items=[InvoiceItem(description="Material", quantity=Decimal("1"), unit_price=Decimal("200"))],
        created_by=uuid4(),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        is_auto_generated=False,
        refundable_status=refundable_status,
    )


class TestUpdateInvoiceRefundedLock:
    """UpdateInvoiceUseCase must reject any edit on a fully-refunded invoice.

    A refunded invoice's amounts are already reflected in company_refunded_total.
    Mutating the invoice after the company has paid would silently corrupt that total.
    The refund status must be cleared first before any further edits are allowed.
    """

    def test_update_refunded_invoice_raises(self):
        """PATCH on a refunded invoice must raise InvalidInvoiceDataError with lock message."""
        invoice = _make_ms_invoice(refundable_status=RefundableStatus.REFUNDED.value)

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        use_case = UpdateInvoiceUseCase(inv_repo)

        with pytest.raises(InvalidInvoiceDataError, match="Refunded expenses are locked"):
            use_case.execute(UpdateInvoiceRequest(invoice_id=invoice.id, recipient_name="New Name"))

        inv_repo.update.assert_not_called()

    def test_update_refunded_invoice_items_raises(self):
        """PATCH items on a refunded invoice must also be blocked."""
        invoice = _make_ms_invoice(refundable_status=RefundableStatus.REFUNDED.value)

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        use_case = UpdateInvoiceUseCase(inv_repo)

        with pytest.raises(InvalidInvoiceDataError, match="Refunded expenses are locked"):
            use_case.execute(
                UpdateInvoiceRequest(
                    invoice_id=invoice.id,
                    items=[{"description": "Changed", "quantity": 1, "unit_price": 999}],
                )
            )

    def test_update_refundable_invoice_succeeds(self):
        """PATCH on a refundable (not yet refunded) invoice must still be allowed."""
        invoice = _make_ms_invoice(refundable_status=RefundableStatus.REFUNDABLE.value)

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        inv_repo.update.side_effect = lambda inv: inv
        use_case = UpdateInvoiceUseCase(inv_repo)

        result = use_case.execute(UpdateInvoiceRequest(invoice_id=invoice.id, recipient_name="Updated"))

        assert result is not None
        inv_repo.update.assert_called_once()

    def test_update_refund_pending_invoice_succeeds(self):
        """PATCH on a refund_pending invoice must still be allowed."""
        invoice = _make_ms_invoice(refundable_status=RefundableStatus.REFUND_PENDING.value)

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        inv_repo.update.side_effect = lambda inv: inv
        use_case = UpdateInvoiceUseCase(inv_repo)

        result = use_case.execute(UpdateInvoiceRequest(invoice_id=invoice.id, recipient_name="Updated"))

        assert result is not None
        inv_repo.update.assert_called_once()

    def test_update_no_refundable_status_invoice_succeeds(self):
        """PATCH on an invoice with no refundable_status (None) must still be allowed."""
        invoice = _make_ms_invoice(refundable_status=None)

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        inv_repo.update.side_effect = lambda inv: inv
        use_case = UpdateInvoiceUseCase(inv_repo)

        result = use_case.execute(UpdateInvoiceRequest(invoice_id=invoice.id, recipient_name="Updated"))

        assert result is not None
        inv_repo.update.assert_called_once()


class TestDeleteInvoiceRefundedLock:
    """DeleteInvoiceUseCase must reject deletion of a fully-refunded invoice.

    A refunded invoice is the audit record for what the company paid back.
    Deleting it would silently drop the record from company_refunded_total
    with no recovery path. The refund status must be cleared before deletion.
    """

    def test_delete_refunded_invoice_raises(self):
        """DELETE on a refunded invoice must raise InvalidInvoiceDataError with lock message."""
        invoice = _make_ms_invoice(refundable_status=RefundableStatus.REFUNDED.value)

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        use_case = DeleteInvoiceUseCase(inv_repo)

        with pytest.raises(InvalidInvoiceDataError, match="Refunded expenses are locked"):
            use_case.execute(invoice.id)

        inv_repo.delete.assert_not_called()

    def test_delete_refundable_invoice_succeeds(self):
        """DELETE on a refundable (not yet refunded) invoice must proceed normally."""
        invoice = _make_ms_invoice(refundable_status=RefundableStatus.REFUNDABLE.value)

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        use_case = DeleteInvoiceUseCase(inv_repo)

        use_case.execute(invoice.id)

        inv_repo.delete.assert_called_once_with(invoice.id)

    def test_delete_refund_pending_invoice_succeeds(self):
        """DELETE on a refund_pending invoice must proceed normally."""
        invoice = _make_ms_invoice(refundable_status=RefundableStatus.REFUND_PENDING.value)

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        use_case = DeleteInvoiceUseCase(inv_repo)

        use_case.execute(invoice.id)

        inv_repo.delete.assert_called_once_with(invoice.id)

    def test_delete_no_refundable_status_invoice_succeeds(self):
        """DELETE on an invoice with no refundable_status (None) must proceed normally."""
        invoice = _make_ms_invoice(refundable_status=None)

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        use_case = DeleteInvoiceUseCase(inv_repo)

        use_case.execute(invoice.id)

        inv_repo.delete.assert_called_once_with(invoice.id)


# ---------------------------------------------------------------------------
# service_month — CreateInvoice / UpdateInvoice
# ---------------------------------------------------------------------------


class TestCreateInvoiceServiceMonth:
    """CreateInvoiceUseCase must normalize service_month and gate it to labor invoices."""

    def test_labor_invoice_service_month_normalized_to_first_of_month(self):
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)

        result = use_case.execute(make_request(type=InvoiceType.LABOR, service_month=date(2026, 6, 15)))

        assert result.service_month == "2026-06-01"

    def test_labor_invoice_without_service_month_is_none(self):
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)

        result = use_case.execute(make_request(type=InvoiceType.LABOR))

        assert result.service_month is None

    def test_non_labor_invoice_with_service_month_raises(self):
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)

        with pytest.raises(ServiceMonthNotAllowedError):
            use_case.execute(make_request(type=InvoiceType.MATERIALS_SERVICES, service_month=date(2026, 6, 1)))

        repo.create.assert_not_called()


class TestUpdateInvoiceServiceMonth:
    """UpdateInvoiceUseCase must thread service_month through with_updates correctly."""

    def test_patch_only_service_month_leaves_other_fields_untouched(self):
        project_id = uuid4()
        invoice = _make_invoice(
            project_id,
            type=InvoiceType.LABOR,
            recipient_name="Untouched Recipient",
            notes="Untouched notes",
        )

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        inv_repo.update.side_effect = lambda inv: inv
        use_case = UpdateInvoiceUseCase(inv_repo)

        result = use_case.execute(UpdateInvoiceRequest(invoice_id=invoice.id, service_month=date(2026, 3, 10)))

        assert result.service_month == "2026-03-01"
        assert result.recipient_name == "Untouched Recipient"
        assert result.notes == "Untouched notes"
        assert result.items[0].description == "Work"

    def test_patch_service_month_null_clears_it(self):
        project_id = uuid4()
        invoice = _make_invoice(project_id, type=InvoiceType.LABOR, service_month=date(2026, 5, 1))

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        inv_repo.update.side_effect = lambda inv: inv
        use_case = UpdateInvoiceUseCase(inv_repo)

        result = use_case.execute(UpdateInvoiceRequest(invoice_id=invoice.id, service_month=None))

        assert result.service_month is None

    def test_patch_unset_service_month_leaves_it_unchanged(self):
        """service_month not provided (_UNSET) must not touch the stored value."""
        project_id = uuid4()
        invoice = _make_invoice(project_id, type=InvoiceType.LABOR, service_month=date(2026, 5, 1))

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        inv_repo.update.side_effect = lambda inv: inv
        use_case = UpdateInvoiceUseCase(inv_repo)

        result = use_case.execute(UpdateInvoiceRequest(invoice_id=invoice.id, recipient_name="New Name"))

        assert result.service_month == "2026-05-01"

    def test_patch_type_away_from_labor_clears_stored_service_month(self):
        """Changing type away from labor without touching service_month must clear it server-side."""
        project_id = uuid4()
        invoice = _make_invoice(project_id, type=InvoiceType.LABOR, service_month=date(2026, 4, 1))

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        inv_repo.update.side_effect = lambda inv: inv
        use_case = UpdateInvoiceUseCase(inv_repo)

        result = use_case.execute(UpdateInvoiceRequest(invoice_id=invoice.id, type=InvoiceType.OTHERS))

        assert result.type == "others"
        assert result.service_month is None

    def test_patch_setting_service_month_on_non_labor_invoice_raises(self):
        project_id = uuid4()
        invoice = _make_invoice(project_id, type=InvoiceType.MATERIALS_SERVICES)

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        use_case = UpdateInvoiceUseCase(inv_repo)

        with pytest.raises(ServiceMonthNotAllowedError):
            use_case.execute(UpdateInvoiceRequest(invoice_id=invoice.id, service_month=date(2026, 6, 1)))

        inv_repo.update.assert_not_called()

    def test_patch_type_to_labor_and_service_month_together_succeeds(self):
        """Setting type=labor and service_month in the same PATCH must succeed (effective_type)."""
        project_id = uuid4()
        invoice = _make_invoice(project_id, type=InvoiceType.OTHERS)

        inv_repo = MagicMock(spec=IInvoiceRepository)
        inv_repo.find_by_id.return_value = invoice
        inv_repo.update.side_effect = lambda inv: inv
        use_case = UpdateInvoiceUseCase(inv_repo)

        result = use_case.execute(
            UpdateInvoiceRequest(
                invoice_id=invoice.id,
                type=InvoiceType.LABOR,
                service_month=date(2026, 7, 5),
            )
        )

        assert result.type == "labor"
        assert result.service_month == "2026-07-01"
