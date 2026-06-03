"""Unit tests for CreateInvoiceUseCase."""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, Mock
from uuid import uuid4
import pytest

from app.application.invoice.create_invoice import CreateInvoiceUseCase, CreateInvoiceRequest
from app.application.invoice.ports import IInvoiceRepository
from app.application.invoice.update_invoice import UpdateInvoiceUseCase, UpdateInvoiceRequest
from app.application.tags.exceptions import InvalidProjectTagError
from app.domain.entities.invoice import Invoice, InvoiceType
from app.domain.entities.project_tag import ProjectTag
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


def _make_invoice(project_id) -> Invoice:
    """Build a minimal Invoice entity for update tests."""
    from app.domain.value_objects.invoice_item import InvoiceItem
    from decimal import Decimal

    return Invoice(
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
