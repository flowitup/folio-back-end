"""Unit tests for billing totals computation."""

from decimal import Decimal

from app.domain.billing.totals import compute_totals, vat_breakdown
from app.domain.billing.value_objects import BillingDocumentItem


def _item(desc="Widget", qty="1", price="100", vat="20"):
    return BillingDocumentItem(
        description=desc,
        quantity=Decimal(qty),
        unit_price=Decimal(price),
        vat_rate=Decimal(vat),
    )


class TestComputeTotals:
    def test_empty_items(self):
        totals = compute_totals([])
        assert totals.total_ht == Decimal("0")
        assert totals.total_tva == Decimal("0")
        assert totals.total_ttc == Decimal("0")
        assert totals.total_tva_by_rate == {}

    def test_single_item_20pct(self):
        item = _item(qty="2", price="100", vat="20")
        totals = compute_totals([item])
        assert totals.total_ht == Decimal("200")
        assert totals.total_tva == Decimal("40")
        assert totals.total_ttc == Decimal("240")

    def test_totals_mixed_vat_rates(self):
        """Regression: 10% + 20% items → correct separate breakdown sums."""
        item_10 = _item(desc="Service", qty="1", price="100", vat="10")
        item_20 = _item(desc="Product", qty="2", price="150", vat="20")
        totals = compute_totals([item_10, item_20])

        assert totals.total_ht == Decimal("400")  # 100 + 300
        assert totals.total_tva_by_rate[Decimal("10")] == Decimal("10")
        assert totals.total_tva_by_rate[Decimal("20")] == Decimal("60")
        assert totals.total_tva == Decimal("70")
        assert totals.total_ttc == Decimal("470")

    def test_vat_normalization_same_bucket(self):
        """Decimal("20"), Decimal("20.0") should map to the same bucket."""
        item_a = BillingDocumentItem(
            description="A",
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
            vat_rate=Decimal("20"),
        )
        item_b = BillingDocumentItem(
            description="B",
            quantity=Decimal("1"),
            unit_price=Decimal("200"),
            vat_rate=Decimal("20.0"),
        )
        totals = compute_totals([item_a, item_b])
        # Should be one bucket, not two
        assert len(totals.total_tva_by_rate) == 1

    def test_zero_vat(self):
        item = _item(qty="3", price="50", vat="0")
        totals = compute_totals([item])
        assert totals.total_ht == Decimal("150")
        assert totals.total_tva == Decimal("0")
        assert totals.total_ttc == Decimal("150")

    def test_fractional_quantity(self):
        """1.5 hours × 50€ with 20% VAT."""
        item = BillingDocumentItem(
            description="Consulting",
            quantity=Decimal("1.5"),
            unit_price=Decimal("50"),
            vat_rate=Decimal("20"),
        )
        totals = compute_totals([item])
        assert totals.total_ht == Decimal("75")
        assert totals.total_tva == Decimal("15")
        assert totals.total_ttc == Decimal("90")

    def test_decimal_precision_preserved(self):
        """Decimal precision must not be lost through compute → total pipeline."""
        item = BillingDocumentItem(
            description="Precise",
            quantity=Decimal("1"),
            unit_price=Decimal("99.99"),
            vat_rate=Decimal("5.5"),
        )
        totals = compute_totals([item])
        expected_tva = Decimal("99.99") * Decimal("5.5") / Decimal("100")
        assert totals.total_tva == expected_tva


class TestVatBreakdown:
    def test_empty(self):
        assert vat_breakdown([]) == []

    def test_single_rate_breakdown(self):
        item = _item(qty="2", price="100", vat="20")
        result = vat_breakdown([item])
        assert len(result) == 1
        rate, base, tva = result[0]
        assert rate == Decimal("20")
        assert base == Decimal("200")
        assert tva == Decimal("40")

    def test_sorted_descending(self):
        """vat_breakdown returns (rate, base, tva) sorted by rate descending."""
        items = [
            _item(qty="1", price="100", vat="5"),
            _item(qty="1", price="200", vat="20"),
            _item(qty="1", price="50", vat="10"),
        ]
        result = vat_breakdown(items)
        rates = [r[0] for r in result]
        assert rates == sorted(rates, reverse=True)

    def test_mixed_rates_grouped(self):
        """Two items at 20%, one at 10% → two buckets in breakdown."""
        items = [
            _item(qty="1", price="100", vat="20"),
            _item(qty="2", price="50", vat="20"),
            _item(qty="1", price="200", vat="10"),
        ]
        result = vat_breakdown(items)
        assert len(result) == 2
        # 20% bucket: base=200, tva=40
        twenty = next(r for r in result if r[0] == Decimal("20"))
        assert twenty[1] == Decimal("200")
        assert twenty[2] == Decimal("40")
        # 10% bucket: base=200, tva=20
        ten = next(r for r in result if r[0] == Decimal("10"))
        assert ten[1] == Decimal("200")
        assert ten[2] == Decimal("20")
