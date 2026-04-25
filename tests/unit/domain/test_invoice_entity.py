"""Unit tests for Invoice domain entity."""

from decimal import Decimal
from datetime import date, datetime, timezone
from uuid import uuid4
import pytest

from app.domain.entities.invoice import Invoice, InvoiceType
from app.domain.value_objects.invoice_item import InvoiceItem


def make_invoice(**kwargs):
    """Factory helper to create test invoice with sensible defaults."""
    defaults = dict(
        id=uuid4(), project_id=uuid4(), invoice_number="INV-2026-0001",
        type=InvoiceType.CLIENT, issue_date=date.today(),
        recipient_name="ACME Corp", created_by=uuid4(),
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        items=[]
    )
    defaults.update(kwargs)
    return Invoice(**defaults)


class TestInvoiceTotalAmount:
    """Tests for Invoice.total_amount property."""

    def test_empty_items_total_zero(self):
        """Total should be 0 when no items."""
        invoice = make_invoice(items=[])
        assert invoice.total_amount == Decimal("0")

    def test_single_item_total(self):
        """Total should sum single item."""
        item = InvoiceItem(description="Work", quantity=Decimal("2"), unit_price=Decimal("100"))
        invoice = make_invoice(items=[item])
        assert invoice.total_amount == Decimal("200")

    def test_multiple_items_sum(self):
        """Total should sum all items."""
        items = [
            InvoiceItem(description="A", quantity=Decimal("2"), unit_price=Decimal("100")),
            InvoiceItem(description="B", quantity=Decimal("1"), unit_price=Decimal("50")),
        ]
        invoice = make_invoice(items=items)
        assert invoice.total_amount == Decimal("250")

    def test_total_with_decimal_precision(self):
        """Total should preserve Decimal precision."""
        items = [
            InvoiceItem(description="X", quantity=Decimal("3"), unit_price=Decimal("10.50")),
            InvoiceItem(description="Y", quantity=Decimal("2"), unit_price=Decimal("7.25")),
        ]
        invoice = make_invoice(items=items)
        expected = Decimal("3") * Decimal("10.50") + Decimal("2") * Decimal("7.25")
        assert invoice.total_amount == expected


class TestInvoiceItemTotal:
    """Tests for InvoiceItem.total property."""

    def test_item_total_computed(self):
        """Item total should be quantity * unit_price."""
        item = InvoiceItem(description="X", quantity=Decimal("3"), unit_price=Decimal("10"))
        assert item.total == Decimal("30")

    def test_item_total_decimal_precision(self):
        """Item total preserves Decimal precision."""
        item = InvoiceItem(
            description="Y", quantity=Decimal("2.5"), unit_price=Decimal("12.40")
        )
        assert item.total == Decimal("31.00")

    def test_item_total_zero_quantity(self):
        """Item total is 0 when quantity is 0."""
        item = InvoiceItem(description="Z", quantity=Decimal("0"), unit_price=Decimal("100"))
        assert item.total == Decimal("0")


class TestInvoiceEquality:
    """Tests for Invoice equality based on id."""

    def test_same_id_equal(self):
        """Two invoices with same id are equal."""
        id_ = uuid4()
        inv1 = make_invoice(id=id_, recipient_name="A")
        inv2 = make_invoice(id=id_, recipient_name="B")
        assert inv1 == inv2

    def test_different_ids_not_equal(self):
        """Two invoices with different ids are not equal."""
        assert make_invoice() != make_invoice()

    def test_equality_ignores_other_fields(self):
        """Equality only compares id, ignores other fields."""
        id_ = uuid4()
        inv1 = make_invoice(
            id=id_, recipient_name="A", invoice_number="INV-001",
            type=InvoiceType.CLIENT
        )
        inv2 = make_invoice(
            id=id_, recipient_name="B", invoice_number="INV-002",
            type=InvoiceType.LABOR
        )
        assert inv1 == inv2  # Same id means equal

    def test_not_equal_to_other_types(self):
        """Invoice not equal to non-Invoice objects."""
        inv = make_invoice()
        assert inv != "not an invoice"
        assert inv != 123
        assert inv != None


class TestInvoiceHash:
    """Tests for Invoice hash based on id."""

    def test_hash_based_on_id(self):
        """Invoice hash should be based on id."""
        id_ = uuid4()
        inv1 = make_invoice(id=id_)
        inv2 = make_invoice(id=id_)
        assert hash(inv1) == hash(inv2)

    def test_different_ids_different_hash(self):
        """Different ids should (likely) have different hashes."""
        inv1 = make_invoice()
        inv2 = make_invoice()
        # Not guaranteed but extremely likely for different UUIDs
        assert hash(inv1) != hash(inv2)

    def test_usable_in_set(self):
        """Invoice should be usable in sets due to __hash__."""
        id_ = uuid4()
        inv1 = make_invoice(id=id_)
        inv2 = make_invoice(id=id_)
        inv_set = {inv1, inv2}
        assert len(inv_set) == 1  # Same id means same item in set


class TestInvoiceType:
    """Tests for InvoiceType enum."""

    def test_invoice_type_client_value(self):
        """CLIENT type should have correct string value."""
        assert InvoiceType.CLIENT.value == "client"

    def test_invoice_type_labor_value(self):
        """LABOR type should have correct string value."""
        assert InvoiceType.LABOR.value == "labor"

    def test_invoice_type_supplier_value(self):
        """SUPPLIER type should have correct string value."""
        assert InvoiceType.SUPPLIER.value == "supplier"

    def test_invoice_type_from_string(self):
        """Can construct InvoiceType from string value."""
        assert InvoiceType("client") == InvoiceType.CLIENT
        assert InvoiceType("labor") == InvoiceType.LABOR
        assert InvoiceType("supplier") == InvoiceType.SUPPLIER


class TestInvoiceCreation:
    """Tests for invoice creation and field validation."""

    def test_create_with_all_fields(self):
        """Can create invoice with all optional fields."""
        id_ = uuid4()
        project_id = uuid4()
        created_by = uuid4()
        now = datetime.now(timezone.utc)

        invoice = Invoice(
            id=id_,
            project_id=project_id,
            invoice_number="INV-001",
            type=InvoiceType.CLIENT,
            issue_date=date.today(),
            recipient_name="Client A",
            recipient_address="123 Main St",
            notes="Test note",
            created_by=created_by,
            created_at=now,
            updated_at=now,
            items=[],
        )

        assert invoice.id == id_
        assert invoice.project_id == project_id
        assert invoice.invoice_number == "INV-001"
        assert invoice.type == InvoiceType.CLIENT
        assert invoice.recipient_name == "Client A"
        assert invoice.recipient_address == "123 Main St"
        assert invoice.notes == "Test note"

    def test_create_without_optional_fields(self):
        """Can create invoice without optional fields."""
        invoice = make_invoice()
        assert invoice.recipient_address is None
        assert invoice.notes is None
