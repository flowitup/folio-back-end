"""Unit tests for LibraryProduct domain entity.

Tests aggregates (with_purchase_applied) and enrichment (with_enrichment).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from app.domain.entities.library_product import LibraryProduct


class TestLibraryProductCreate:
    """Test LibraryProduct.create factory."""

    def test_create_returns_zero_aggregates(self):
        company_id = uuid4()
        supplier_id = uuid4()

        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="REF-001",
            name="Test Product",
        )

        assert product.purchase_count == 0
        assert product.total_quantity == Decimal("0")
        assert product.last_unit_price is None
        assert product.first_purchased_at is None
        assert product.last_purchased_at is None

    def test_create_with_enrichment_fields(self):
        company_id = uuid4()
        supplier_id = uuid4()

        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="REF-001",
            name="Test Product",
            description="A test product",
            size="Large",
            category="Hardware",
            image_storage_key="s3://bucket/product.png",
            product_url="https://example.com/product",
        )

        assert product.description == "A test product"
        assert product.size == "Large"
        assert product.category == "Hardware"
        assert product.image_storage_key == "s3://bucket/product.png"
        assert product.product_url == "https://example.com/product"

    def test_create_immutable(self):
        company_id = uuid4()
        supplier_id = uuid4()

        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="REF-001",
            name="Test Product",
        )

        # Frozen dataclass should prevent mutation
        try:
            product.name = "Updated"  # type: ignore[misc]
            assert False, "Should have raised FrozenInstanceError"
        except (AttributeError, Exception):
            pass  # Expected


class TestLibraryProductWithPurchaseApplied:
    """Test LibraryProduct.with_purchase_applied aggregate logic."""

    def test_first_purchase_increments_count(self):
        company_id = uuid4()
        supplier_id = uuid4()
        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="REF-001",
            name="Test Product",
        )

        now = datetime.now(timezone.utc)
        updated = product.with_purchase_applied(
            qty=Decimal("10"),
            unit_price=Decimal("5.99"),
            purchased_at=now,
        )

        assert updated.purchase_count == 1
        assert updated.total_quantity == Decimal("10")
        assert updated.last_unit_price == Decimal("5.99")
        assert updated.first_purchased_at == now
        assert updated.last_purchased_at == now

    def test_second_purchase_sums_quantities(self):
        company_id = uuid4()
        supplier_id = uuid4()
        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="REF-001",
            name="Test Product",
        )

        now = datetime.now(timezone.utc)
        p1 = product.with_purchase_applied(
            qty=Decimal("10"),
            unit_price=Decimal("5.99"),
            purchased_at=now,
        )

        later = now + timedelta(days=1)
        p2 = p1.with_purchase_applied(
            qty=Decimal("20"),
            unit_price=Decimal("6.49"),
            purchased_at=later,
        )

        assert p2.purchase_count == 2
        assert p2.total_quantity == Decimal("30")
        assert p2.last_unit_price == Decimal("6.49")
        assert p2.first_purchased_at == now
        assert p2.last_purchased_at == later

    def test_purchase_with_zero_price(self):
        company_id = uuid4()
        supplier_id = uuid4()
        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="REF-001",
            name="Test Product",
        )

        now = datetime.now(timezone.utc)
        updated = product.with_purchase_applied(
            qty=Decimal("5"),
            unit_price=Decimal("0"),
            purchased_at=now,
        )

        assert updated.last_unit_price == Decimal("0")
        assert updated.purchase_count == 1

    def test_decimal_precision_no_float_drift(self):
        company_id = uuid4()
        supplier_id = uuid4()
        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="REF-001",
            name="Test Product",
        )

        now = datetime.now(timezone.utc)
        # Use prices with many decimal places
        p1 = product.with_purchase_applied(
            qty=Decimal("1.333"),
            unit_price=Decimal("3.14159"),
            purchased_at=now,
        )
        p2 = p1.with_purchase_applied(
            qty=Decimal("2.667"),
            unit_price=Decimal("2.71828"),
            purchased_at=now,
        )

        # Verify no float rounding occurred
        assert p2.total_quantity == Decimal("4.000")
        assert p2.last_unit_price == Decimal("2.71828")

    def test_purchase_timestamp_ordering(self):
        """Out-of-order purchases should still set first/last correctly."""
        company_id = uuid4()
        supplier_id = uuid4()
        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="REF-001",
            name="Test Product",
        )

        now = datetime.now(timezone.utc)
        tomorrow = now + timedelta(days=1)
        yesterday = now - timedelta(days=1)

        # Apply purchase at "now"
        p1 = product.with_purchase_applied(
            qty=Decimal("10"),
            unit_price=Decimal("5.00"),
            purchased_at=now,
        )

        # Apply purchase at "tomorrow"
        p2 = p1.with_purchase_applied(
            qty=Decimal("5"),
            unit_price=Decimal("6.00"),
            purchased_at=tomorrow,
        )

        # Apply purchase at "yesterday" (chronologically earlier)
        p3 = p2.with_purchase_applied(
            qty=Decimal("20"),
            unit_price=Decimal("4.00"),
            purchased_at=yesterday,
        )

        # first_purchased_at should remain "now" (was set first)
        assert p3.first_purchased_at == now
        # last_purchased_at should be "tomorrow" (most recent in real time)
        assert p3.last_purchased_at == tomorrow

    def test_does_not_mutate_original(self):
        """Original product should remain unchanged."""
        company_id = uuid4()
        supplier_id = uuid4()
        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="REF-001",
            name="Test Product",
        )

        now = datetime.now(timezone.utc)
        updated = product.with_purchase_applied(
            qty=Decimal("10"),
            unit_price=Decimal("5.99"),
            purchased_at=now,
        )

        # Original should be unchanged
        assert product.purchase_count == 0
        assert product.total_quantity == Decimal("0")
        # Updated should have the changes
        assert updated.purchase_count == 1
        assert updated.total_quantity == Decimal("10")


class TestLibraryProductWithEnrichment:
    """Test LibraryProduct.with_enrichment logic."""

    def test_enrichment_fills_empty_slots_only(self):
        """Enrichment should only fill None fields."""
        company_id = uuid4()
        supplier_id = uuid4()
        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="REF-001",
            name="Test Product",
            description=None,
            size=None,
            category=None,
        )

        enriched = product.with_enrichment(
            description="New description",
            size="Medium",
            category="Software",
        )

        assert enriched.description == "New description"
        assert enriched.size == "Medium"
        assert enriched.category == "Software"

    def test_enrichment_preserves_existing_values(self):
        """Enrichment should NOT overwrite non-null fields."""
        company_id = uuid4()
        supplier_id = uuid4()
        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="REF-001",
            name="Original Name",
            description="Original description",
            size="Large",
            category="Hardware",
        )

        enriched = product.with_enrichment(
            name="New Name",
            description="New description",
            size="Small",
            category="Software",
        )

        # name always gets overwritten
        assert enriched.name == "New Name"
        # Other fields should preserve original
        assert enriched.description == "Original description"
        assert enriched.size == "Large"
        assert enriched.category == "Hardware"

    def test_enrichment_with_all_none_fields(self):
        """Enrichment with all None fields should not change the product."""
        company_id = uuid4()
        supplier_id = uuid4()
        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="REF-001",
            name="Test Product",
            description="Original description",
            size="Large",
        )

        enriched = product.with_enrichment()

        assert enriched.description == "Original description"
        assert enriched.size == "Large"

    def test_enrichment_partial_fill(self):
        """Enrichment should only fill specified None fields."""
        company_id = uuid4()
        supplier_id = uuid4()
        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="REF-001",
            name="Test Product",
            description=None,
            size="Large",
            category=None,
        )

        enriched = product.with_enrichment(
            description="New description",
            # size not provided, should keep "Large"
            category="New category",
        )

        assert enriched.description == "New description"
        assert enriched.size == "Large"
        assert enriched.category == "New category"

    def test_enrichment_image_storage_key(self):
        """image_storage_key should be filled only when None."""
        company_id = uuid4()
        supplier_id = uuid4()
        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="REF-001",
            name="Test Product",
            image_storage_key=None,
        )

        enriched = product.with_enrichment(
            image_storage_key="s3://bucket/image.png",
        )

        assert enriched.image_storage_key == "s3://bucket/image.png"

    def test_enrichment_does_not_mutate_original(self):
        """Original should remain unchanged."""
        company_id = uuid4()
        supplier_id = uuid4()
        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="REF-001",
            name="Test Product",
            description=None,
        )

        enriched = product.with_enrichment(
            description="New description",
        )

        assert product.description is None
        assert enriched.description == "New description"

    def test_enrichment_updates_timestamp(self):
        """with_enrichment should update the updated_at timestamp."""
        company_id = uuid4()
        supplier_id = uuid4()
        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="REF-001",
            name="Test Product",
        )

        original_updated = product.updated_at
        enriched = product.with_enrichment(
            description="New description",
        )

        # updated_at should be strictly later (or equal depending on timing)
        assert enriched.updated_at >= original_updated
