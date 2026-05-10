"""Unit tests for billing domain value objects (BillingDocumentItem, DocumentTotals)."""

from decimal import Decimal

import pytest

from app.domain.billing.value_objects import BillingDocumentItem, DocumentTotals


class TestBillingDocumentItem:
    def _make(self, qty="2", price="50", vat="20"):
        return BillingDocumentItem(
            description="Test item",
            quantity=Decimal(qty),
            unit_price=Decimal(price),
            vat_rate=Decimal(vat),
        )

    def test_total_ht(self):
        item = self._make(qty="3", price="100")
        assert item.total_ht == Decimal("300")

    def test_total_tva_20pct(self):
        item = self._make(qty="1", price="200", vat="20")
        assert item.total_tva == Decimal("40")

    def test_total_ttc(self):
        item = self._make(qty="2", price="100", vat="20")
        # HT=200, TVA=40, TTC=240
        assert item.total_ttc == Decimal("240")

    def test_zero_vat(self):
        item = self._make(qty="1", price="500", vat="0")
        assert item.total_tva == Decimal("0")
        assert item.total_ttc == item.total_ht

    def test_frozen(self):
        """BillingDocumentItem is a frozen dataclass — mutations raise."""
        item = self._make()
        with pytest.raises((AttributeError, TypeError)):
            item.description = "Mutated"  # type: ignore[misc]

    def test_equality_by_value(self):
        """Two items with identical fields are equal (dataclass default)."""
        a = self._make()
        b = self._make()
        assert a == b

    def test_fractional_quantity_precision(self):
        """1.5 × 66.66 stays in Decimal; no float drift."""
        item = BillingDocumentItem(
            description="Consulting",
            quantity=Decimal("1.5"),
            unit_price=Decimal("66.66"),
            vat_rate=Decimal("10"),
        )
        assert item.total_ht == Decimal("1.5") * Decimal("66.66")


class TestBillingDocumentItemCategory:
    """Phase 01 — category field backward-compat semantics."""

    def test_category_trimmed(self):
        item = BillingDocumentItem(
            description="Dépose toiture",
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
            vat_rate=Decimal("10"),
            category=" Toiture  ",
        )
        assert item.category == "Toiture"

    def test_category_empty_coerced_to_none(self):
        item = BillingDocumentItem(
            description="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
            vat_rate=Decimal("20"),
            category="",
        )
        assert item.category is None

    def test_category_whitespace_coerced_to_none(self):
        item = BillingDocumentItem(
            description="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
            vat_rate=Decimal("20"),
            category="   ",
        )
        assert item.category is None

    def test_category_over_120_raises(self):
        with pytest.raises(ValueError, match="category exceeds 120 characters"):
            BillingDocumentItem(
                description="Service",
                quantity=Decimal("1"),
                unit_price=Decimal("100"),
                vat_rate=Decimal("20"),
                category="x" * 121,
            )

    def test_category_none_default(self):
        item = BillingDocumentItem(
            description="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
            vat_rate=Decimal("20"),
        )
        assert item.category is None

    def test_category_exactly_120_ok(self):
        item = BillingDocumentItem(
            description="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
            vat_rate=Decimal("20"),
            category="x" * 120,
        )
        assert item.category == "x" * 120


class TestDocumentTotals:
    def test_frozen(self):
        totals = DocumentTotals(
            total_ht=Decimal("100"),
            total_tva_by_rate={Decimal("20"): Decimal("20")},
            total_tva=Decimal("20"),
            total_ttc=Decimal("120"),
        )
        with pytest.raises((AttributeError, TypeError)):
            totals.total_ht = Decimal("999")  # type: ignore[misc]
