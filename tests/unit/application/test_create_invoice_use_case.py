"""Unit tests for CreateInvoiceUseCase."""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4
import pytest

from app.application.invoice.create_invoice import CreateInvoiceUseCase, CreateInvoiceRequest
from app.application.invoice.ports import IInvoiceRepository
from app.domain.entities.invoice import Invoice, InvoiceType
from app.domain.exceptions.invoice_exceptions import InvalidInvoiceDataError


def make_mock_repo():
    """Create mock repository with sensible defaults."""
    repo = MagicMock(spec=IInvoiceRepository)
    repo.next_invoice_number.return_value = "INV-2026-0001"
    repo.create.side_effect = lambda inv: inv  # return as-is
    return repo


def make_request(**kwargs):
    """Factory helper for test requests."""
    defaults = dict(
        project_id=uuid4(), created_by=uuid4(),
        type=InvoiceType.CLIENT, issue_date=date.today(),
        recipient_name="ACME Corp",
        items=[{"description": "Work", "quantity": 10, "unit_price": 50}]
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
        assert result.type == "client"
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
        request = make_request(
            recipient_address="123 Main St",
            notes="Important invoice"
        )

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
        request = make_request(
            items=[{"description": "Work", "quantity": 2.5, "unit_price": 10.50}]
        )

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
            use_case.execute(
                make_request(items=[{"description": "X", "quantity": 0, "unit_price": 10}])
            )

    def test_negative_quantity_raises(self):
        """Should reject item with negative quantity."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)

        with pytest.raises(InvalidInvoiceDataError, match="[Qq]uantity"):
            use_case.execute(
                make_request(items=[{"description": "X", "quantity": -1, "unit_price": 10}])
            )

    def test_negative_price_raises(self):
        """Should reject item with negative price."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)

        with pytest.raises(InvalidInvoiceDataError, match="[Pp]rice|[Uu]nit"):
            use_case.execute(
                make_request(items=[{"description": "X", "quantity": 1, "unit_price": -5}])
            )

    def test_empty_description_raises(self):
        """Should reject item with empty description."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)

        with pytest.raises(InvalidInvoiceDataError, match="[Dd]escription"):
            use_case.execute(
                make_request(items=[{"description": "", "quantity": 1, "unit_price": 10}])
            )

    def test_whitespace_description_raises(self):
        """Should reject item with whitespace-only description."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)

        with pytest.raises(InvalidInvoiceDataError, match="[Dd]escription"):
            use_case.execute(
                make_request(items=[{"description": "   ", "quantity": 1, "unit_price": 10}])
            )

    def test_zero_price_allowed(self):
        """Should allow zero price (e.g., for donation/no-charge items)."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)
        request = make_request(
            items=[{"description": "X", "quantity": 1, "unit_price": 0}]
        )

        result = use_case.execute(request)

        assert result.total_amount == 0.0


class TestCreateInvoiceInvoiceType:
    """Tests for different invoice types."""

    def test_create_client_invoice(self):
        """Should create client invoice type."""
        repo = make_mock_repo()
        use_case = CreateInvoiceUseCase(repo)
        request = make_request(type=InvoiceType.CLIENT)

        result = use_case.execute(request)

        assert result.type == "client"

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
        request = make_request(type=InvoiceType.SUPPLIER)

        result = use_case.execute(request)

        assert result.type == "supplier"
