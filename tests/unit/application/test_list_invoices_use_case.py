"""Unit tests for ListInvoicesUseCase."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from app.application.invoice.list_invoices import ListInvoicesUseCase, ListInvoicesRequest
from app.application.invoice.ports import IInvoiceRepository
from app.domain.entities.invoice import Invoice, InvoiceType
from app.domain.value_objects.invoice_item import InvoiceItem


def make_invoice(type_=InvoiceType.RELEASED_FUNDS, invoice_number="INV-2026-0001"):
    """Factory helper for test invoices."""
    return Invoice(
        id=uuid4(),
        project_id=uuid4(),
        invoice_number=invoice_number,
        type=type_,
        issue_date=date.today(),
        recipient_name="Acme",
        created_by=uuid4(),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        items=[InvoiceItem("Work", Decimal("1"), Decimal("100"))],
    )


class TestListInvoicesBasic:
    """Basic tests for ListInvoicesUseCase."""

    def test_list_returns_all_when_no_filter(self):
        """Should return all invoices when no type filter."""
        repo = MagicMock(spec=IInvoiceRepository)
        invoices = [make_invoice(), make_invoice(invoice_number="INV-2026-0002")]
        repo.list_by_project.return_value = invoices

        use_case = ListInvoicesUseCase(repo)
        project_id = uuid4()
        request = ListInvoicesRequest(project_id=project_id)
        result = use_case.execute(request)

        assert len(result) == 2
        assert result[0].invoice_number == "INV-2026-0001"
        assert result[1].invoice_number == "INV-2026-0002"
        repo.list_by_project.assert_called_once_with(project_id, None)

    def test_list_returns_empty_list(self):
        """Should return empty list when no invoices found."""
        repo = MagicMock(spec=IInvoiceRepository)
        repo.list_by_project.return_value = []

        use_case = ListInvoicesUseCase(repo)
        project_id = uuid4()
        request = ListInvoicesRequest(project_id=project_id)
        result = use_case.execute(request)

        assert result == []
        repo.list_by_project.assert_called_once_with(project_id, None)


class TestListInvoicesFiltering:
    """Tests for invoice type filtering."""

    def test_list_passes_type_filter_to_repo(self):
        """Should pass invoice_type filter to repository."""
        repo = MagicMock(spec=IInvoiceRepository)
        labor_invoices = [make_invoice(InvoiceType.LABOR)]
        repo.list_by_project.return_value = labor_invoices

        use_case = ListInvoicesUseCase(repo)
        project_id = uuid4()
        request = ListInvoicesRequest(project_id=project_id, invoice_type=InvoiceType.LABOR)
        result = use_case.execute(request)

        assert len(result) == 1
        assert result[0].type == "labor"
        repo.list_by_project.assert_called_once_with(project_id, InvoiceType.LABOR)

    def test_list_client_invoices(self):
        """Should filter and return client invoices."""
        repo = MagicMock(spec=IInvoiceRepository)
        client_invoices = [
            make_invoice(InvoiceType.RELEASED_FUNDS, "INV-001"),
            make_invoice(InvoiceType.RELEASED_FUNDS, "INV-002"),
        ]
        repo.list_by_project.return_value = client_invoices

        use_case = ListInvoicesUseCase(repo)
        project_id = uuid4()
        request = ListInvoicesRequest(project_id=project_id, invoice_type=InvoiceType.RELEASED_FUNDS)
        result = use_case.execute(request)

        assert len(result) == 2
        for inv in result:
            assert inv.type == "released_funds"

    def test_list_supplier_invoices(self):
        """Should filter and return supplier invoices."""
        repo = MagicMock(spec=IInvoiceRepository)
        supplier_invoices = [make_invoice(InvoiceType.SUPPLIER)]
        repo.list_by_project.return_value = supplier_invoices

        use_case = ListInvoicesUseCase(repo)
        project_id = uuid4()
        request = ListInvoicesRequest(project_id=project_id, invoice_type=InvoiceType.SUPPLIER)
        result = use_case.execute(request)

        assert len(result) == 1
        assert result[0].type == "supplier"


class TestListInvoicesResponseFormat:
    """Tests for response DTO conversion."""

    def test_response_includes_all_fields(self):
        """Response should include all invoice fields."""
        repo = MagicMock(spec=IInvoiceRepository)
        created_by = uuid4()
        invoice = Invoice(
            id=uuid4(),
            project_id=uuid4(),
            invoice_number="INV-2026-0001",
            type=InvoiceType.RELEASED_FUNDS,
            issue_date=date.today(),
            recipient_name="Test Client",
            recipient_address="123 Main St",
            notes="Test notes",
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            items=[InvoiceItem("Item A", Decimal("1"), Decimal("100"))],
        )
        repo.list_by_project.return_value = [invoice]

        use_case = ListInvoicesUseCase(repo)
        result = use_case.execute(ListInvoicesRequest(project_id=uuid4()))

        assert len(result) == 1
        response = result[0]
        assert response.invoice_number == "INV-2026-0001"
        assert response.type == "released_funds"
        assert response.recipient_name == "Test Client"
        assert response.recipient_address == "123 Main St"
        assert response.notes == "Test notes"
        assert response.total_amount == 100.0

    def test_response_items_converted_to_dtos(self):
        """Response items should be converted to InvoiceItemResponse."""
        repo = MagicMock(spec=IInvoiceRepository)
        items = [
            InvoiceItem("Item A", Decimal("2"), Decimal("50")),
            InvoiceItem("Item B", Decimal("3"), Decimal("25")),
        ]
        invoice = make_invoice()
        invoice.items = items
        repo.list_by_project.return_value = [invoice]

        use_case = ListInvoicesUseCase(repo)
        result = use_case.execute(ListInvoicesRequest(project_id=uuid4()))

        assert len(result) == 1
        response = result[0]
        assert len(response.items) == 2
        assert response.items[0].description == "Item A"
        assert response.items[0].quantity == 2.0
        assert response.items[0].unit_price == 50.0
        assert response.items[0].total == 100.0
        assert response.items[1].description == "Item B"
        assert response.items[1].total == 75.0

    def test_response_total_amount_calculated(self):
        """Response total_amount should sum all items."""
        repo = MagicMock(spec=IInvoiceRepository)
        items = [
            InvoiceItem("A", Decimal("2"), Decimal("100")),
            InvoiceItem("B", Decimal("1"), Decimal("50")),
        ]
        invoice = make_invoice()
        invoice.items = items
        repo.list_by_project.return_value = [invoice]

        use_case = ListInvoicesUseCase(repo)
        result = use_case.execute(ListInvoicesRequest(project_id=uuid4()))

        assert result[0].total_amount == 250.0


class TestListInvoicesProjectId:
    """Tests for project_id handling."""

    def test_list_by_specific_project(self):
        """Should query invoices for specific project."""
        repo = MagicMock(spec=IInvoiceRepository)
        repo.list_by_project.return_value = [make_invoice()]

        use_case = ListInvoicesUseCase(repo)
        project_id = uuid4()
        request = ListInvoicesRequest(project_id=project_id)
        use_case.execute(request)

        repo.list_by_project.assert_called_once()
        call_args = repo.list_by_project.call_args
        assert call_args[0][0] == project_id

    def test_list_different_projects_separately(self):
        """Should isolate invoices by project."""
        repo = MagicMock(spec=IInvoiceRepository)
        project_a_invoices = [make_invoice()]
        repo.list_by_project.return_value = project_a_invoices

        use_case = ListInvoicesUseCase(repo)
        project_a_id = uuid4()
        project_b_id = uuid4()

        # Query project A
        result_a = use_case.execute(ListInvoicesRequest(project_id=project_a_id))
        assert len(result_a) == 1

        # Query project B (mock returns empty)
        repo.list_by_project.return_value = []
        result_b = use_case.execute(ListInvoicesRequest(project_id=project_b_id))
        assert len(result_b) == 0

        # Verify both calls made with correct project IDs
        assert repo.list_by_project.call_count == 2
        calls = repo.list_by_project.call_args_list
        assert calls[0][0][0] == project_a_id
        assert calls[1][0][0] == project_b_id
