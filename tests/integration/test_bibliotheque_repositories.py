"""Integration tests for bibliotheque repositories.

Tests the repository contracts: suppliers, products, purchases.
Uses SQLite in-memory database with session rollback per test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from app.domain.entities.library_product import LibraryProduct
from app.domain.entities.supplier import Supplier
from app.domain.value_objects.library_purchase import LibraryPurchase
from app.infrastructure.database.repositories.sqlalchemy_bibliotheque_product_repository import (
    SqlAlchemyBibliothequeProductRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_bibliotheque_purchase_repository import (
    SqlAlchemyBibliothequePurchaseRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_bibliotheque_supplier_repository import (
    SqlAlchemyBibliothequeSupplierRepository,
)


class TestSupplierRepository:
    """Test ISupplierRepository contract."""

    def test_get_or_create_creates_new_supplier(self, session):
        repo = SqlAlchemyBibliothequeSupplierRepository(session)
        company_id = uuid4()

        supplier = Supplier.create(
            company_id=company_id,
            name="Acme Widgets",
            slug="acme-widgets",
            website_url="https://acme.com",
        )

        result = repo.get_or_create(supplier)

        assert result.id is not None
        assert result.company_id == company_id
        assert result.name == "Acme Widgets"
        assert result.slug == "acme-widgets"

    def test_get_or_create_idempotent_on_company_slug(self, session):
        repo = SqlAlchemyBibliothequeSupplierRepository(session)
        company_id = uuid4()

        supplier1 = Supplier.create(
            company_id=company_id,
            name="Acme Widgets",
            slug="acme-widgets",
        )
        result1 = repo.get_or_create(supplier1)

        # Same company, same slug, different name
        supplier2 = Supplier.create(
            company_id=company_id,
            name="Acme Corp",
            slug="acme-widgets",
        )
        result2 = repo.get_or_create(supplier2)

        assert result1.id == result2.id
        assert result1.name == result2.name == "Acme Widgets"  # First name preserved

    def test_get_or_create_different_companies_same_slug(self, session):
        repo = SqlAlchemyBibliothequeSupplierRepository(session)
        company1_id = uuid4()
        company2_id = uuid4()

        supplier1 = Supplier.create(
            company_id=company1_id,
            name="Acme Widgets",
            slug="acme-widgets",
        )
        result1 = repo.get_or_create(supplier1)

        supplier2 = Supplier.create(
            company_id=company2_id,
            name="Acme Widgets",
            slug="acme-widgets",
        )
        result2 = repo.get_or_create(supplier2)

        # Same slug but different companies = different suppliers
        assert result1.id != result2.id
        assert result1.company_id == company1_id
        assert result2.company_id == company2_id

    def test_list_by_company_ordered_by_name(self, session):
        repo = SqlAlchemyBibliothequeSupplierRepository(session)
        company_id = uuid4()

        suppliers = [
            Supplier.create(company_id=company_id, name="Zebra Corp", slug="zebra"),
            Supplier.create(company_id=company_id, name="Alpha Inc", slug="alpha"),
            Supplier.create(company_id=company_id, name="Beta Ltd", slug="beta"),
        ]

        for s in suppliers:
            repo.get_or_create(s)

        results = repo.list_by_company(company_id)

        assert len(results) == 3
        assert results[0].name == "Alpha Inc"
        assert results[1].name == "Beta Ltd"
        assert results[2].name == "Zebra Corp"

    def test_list_by_company_excludes_other_companies(self, session):
        repo = SqlAlchemyBibliothequeSupplierRepository(session)
        company1_id = uuid4()
        company2_id = uuid4()

        s1 = Supplier.create(company_id=company1_id, name="Company 1 Supplier", slug="c1-sup")
        s2 = Supplier.create(company_id=company2_id, name="Company 2 Supplier", slug="c2-sup")

        repo.get_or_create(s1)
        repo.get_or_create(s2)

        results = repo.list_by_company(company1_id)

        assert len(results) == 1
        assert results[0].name == "Company 1 Supplier"

    def test_find_by_id(self, session):
        repo = SqlAlchemyBibliothequeSupplierRepository(session)
        company_id = uuid4()

        supplier = Supplier.create(company_id=company_id, name="Test Supplier", slug="test")
        created = repo.get_or_create(supplier)

        found = repo.find_by_id(created.id)

        assert found is not None
        assert found.id == created.id
        assert found.name == "Test Supplier"

    def test_find_by_id_not_found(self, session):
        repo = SqlAlchemyBibliothequeSupplierRepository(session)

        found = repo.find_by_id(uuid4())

        assert found is None

    def test_find_by_slug(self, session):
        repo = SqlAlchemyBibliothequeSupplierRepository(session)
        company_id = uuid4()

        supplier = Supplier.create(company_id=company_id, name="Acme", slug="acme-widgets")
        created = repo.get_or_create(supplier)

        found = repo.find_by_slug(company_id, "acme-widgets")

        assert found is not None
        assert found.id == created.id

    def test_find_by_slug_not_found(self, session):
        repo = SqlAlchemyBibliothequeSupplierRepository(session)
        company_id = uuid4()

        found = repo.find_by_slug(company_id, "nonexistent")

        assert found is None


class TestProductRepository:
    """Test ILibraryProductRepository contract."""

    def test_upsert_creates_new_product(self, session):
        repo = SqlAlchemyBibliothequeProductRepository(session)
        company_id = uuid4()
        supplier_id = uuid4()

        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="SKU-001",
            name="Widget A",
        )

        result = repo.upsert(product)

        assert result.id is not None
        assert result.name == "Widget A"
        assert result.supplier_reference == "SKU-001"

    def test_upsert_updates_existing_product(self, session):
        repo = SqlAlchemyBibliothequeProductRepository(session)
        company_id = uuid4()
        supplier_id = uuid4()

        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="SKU-001",
            name="Widget A",
            description=None,
        )
        created = repo.upsert(product)

        # Update via enrichment
        enriched = created.with_enrichment(description="New description")
        updated = repo.upsert(enriched)

        assert updated.id == created.id
        assert updated.description == "New description"

    def test_find_by_reference(self, session):
        repo = SqlAlchemyBibliothequeProductRepository(session)
        company_id = uuid4()
        supplier_id = uuid4()

        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="SKU-001",
            name="Widget A",
        )
        created = repo.upsert(product)

        found = repo.find_by_reference(company_id, supplier_id, "SKU-001")

        assert found is not None
        assert found.id == created.id

    def test_find_by_reference_not_found(self, session):
        repo = SqlAlchemyBibliothequeProductRepository(session)
        company_id = uuid4()
        supplier_id = uuid4()

        found = repo.find_by_reference(company_id, supplier_id, "NONEXISTENT")

        assert found is None

    def test_find_by_id(self, session):
        repo = SqlAlchemyBibliothequeProductRepository(session)
        company_id = uuid4()
        supplier_id = uuid4()

        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="SKU-001",
            name="Widget A",
        )
        created = repo.upsert(product)

        found = repo.find_by_id(created.id)

        assert found is not None
        assert found.id == created.id

    def test_find_by_id_for_update_locks_row(self, session):
        """find_by_id_for_update should return the row with SELECT FOR UPDATE."""
        repo = SqlAlchemyBibliothequeProductRepository(session)
        company_id = uuid4()
        supplier_id = uuid4()

        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="SKU-001",
            name="Widget A",
        )
        created = repo.upsert(product)

        found = repo.find_by_id_for_update(created.id)

        assert found is not None
        assert found.id == created.id

    def test_list_all_products_for_company(self, session):
        repo = SqlAlchemyBibliothequeProductRepository(session)
        company_id = uuid4()
        supplier_id = uuid4()

        products = [
            LibraryProduct.create(
                company_id=company_id,
                supplier_id=supplier_id,
                supplier_reference="SKU-001",
                name="Product A",
            ),
            LibraryProduct.create(
                company_id=company_id,
                supplier_id=supplier_id,
                supplier_reference="SKU-002",
                name="Product B",
            ),
        ]

        for p in products:
            repo.upsert(p)

        results, total = repo.list(company_id)

        assert total == 2
        assert len(results) == 2

    def test_list_products_pagination(self, session):
        repo = SqlAlchemyBibliothequeProductRepository(session)
        company_id = uuid4()
        supplier_id = uuid4()

        for i in range(30):
            p = LibraryProduct.create(
                company_id=company_id,
                supplier_id=supplier_id,
                supplier_reference=f"SKU-{i:03d}",
                name=f"Product {i}",
            )
            repo.upsert(p)

        results, total = repo.list(company_id, limit=10, offset=0)

        assert total == 30
        assert len(results) == 10

        results_p2, _ = repo.list(company_id, limit=10, offset=10)
        assert len(results_p2) == 10
        # Products on page 2 should be different from page 1
        assert results[0].id != results_p2[0].id

    def test_list_products_filter_by_supplier(self, session):
        repo = SqlAlchemyBibliothequeProductRepository(session)
        company_id = uuid4()
        supplier1_id = uuid4()
        supplier2_id = uuid4()

        p1 = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier1_id,
            supplier_reference="SKU-001",
            name="Product from S1",
        )
        p2 = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier2_id,
            supplier_reference="SKU-002",
            name="Product from S2",
        )

        repo.upsert(p1)
        repo.upsert(p2)

        results, total = repo.list(company_id, supplier_id=supplier1_id)

        assert total == 1
        assert results[0].supplier_id == supplier1_id

    def test_list_products_filter_by_category(self, session):
        repo = SqlAlchemyBibliothequeProductRepository(session)
        company_id = uuid4()
        supplier_id = uuid4()

        p1 = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="SKU-001",
            name="Hammer",
            category="Tools",
        )
        p2 = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="SKU-002",
            name="Screw",
            category="Hardware",
        )

        repo.upsert(p1)
        repo.upsert(p2)

        results, total = repo.list(company_id, category="Tools")

        assert total == 1
        assert results[0].category == "Tools"

    def test_list_products_filter_by_search_query(self, session):
        repo = SqlAlchemyBibliothequeProductRepository(session)
        company_id = uuid4()
        supplier_id = uuid4()

        p1 = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="SKU-001",
            name="Red Widget",
            description="A red colored widget",
        )
        p2 = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="SKU-002",
            name="Blue Gadget",
            description="A blue gadget",
        )

        repo.upsert(p1)
        repo.upsert(p2)

        results, total = repo.list(company_id, q="red")

        assert total == 1
        assert results[0].name == "Red Widget"

    def test_list_products_search_case_insensitive(self, session):
        repo = SqlAlchemyBibliothequeProductRepository(session)
        company_id = uuid4()
        supplier_id = uuid4()

        p = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="SKU-001",
            name="WiDgEt",
        )
        repo.upsert(p)

        results, _ = repo.list(company_id, q="widget")

        assert len(results) == 1
        assert results[0].name == "WiDgEt"

    def test_distinct_categories_sorted(self, session):
        repo = SqlAlchemyBibliothequeProductRepository(session)
        company_id = uuid4()
        supplier_id = uuid4()

        products = [
            LibraryProduct.create(
                company_id=company_id,
                supplier_id=supplier_id,
                supplier_reference="SKU-001",
                name="P1",
                category="Zebra",
            ),
            LibraryProduct.create(
                company_id=company_id,
                supplier_id=supplier_id,
                supplier_reference="SKU-002",
                name="P2",
                category="Alpha",
            ),
            LibraryProduct.create(
                company_id=company_id,
                supplier_id=supplier_id,
                supplier_reference="SKU-003",
                name="P3",
                category="Beta",
            ),
            LibraryProduct.create(
                company_id=company_id,
                supplier_id=supplier_id,
                supplier_reference="SKU-004",
                name="P4",
                category="Alpha",
            ),
        ]

        for p in products:
            repo.upsert(p)

        categories = repo.distinct_categories(company_id)

        # Should be sorted and unique
        assert categories == ["Alpha", "Beta", "Zebra"]

    def test_distinct_categories_excludes_null(self, session):
        repo = SqlAlchemyBibliothequeProductRepository(session)
        company_id = uuid4()
        supplier_id = uuid4()

        p1 = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="SKU-001",
            name="P1",
            category="Tools",
        )
        p2 = LibraryProduct.create(
            company_id=company_id,
            supplier_id=supplier_id,
            supplier_reference="SKU-002",
            name="P2",
            category=None,
        )

        repo.upsert(p1)
        repo.upsert(p2)

        categories = repo.distinct_categories(company_id)

        assert categories == ["Tools"]
        assert None not in categories


class TestPurchaseRepository:
    """Test ILibraryPurchaseRepository contract."""

    def test_add_if_absent_inserts_new_purchase(self, session):
        purchase_repo = SqlAlchemyBibliothequePurchaseRepository(session)
        product_id = uuid4()

        purchase = LibraryPurchase(
            product_id=product_id,
            source_document_ref="TICKET-001",
            source_document_type="ticket",
            line_index=0,
            purchased_at=datetime.now(timezone.utc),
            quantity=Decimal("10"),
            unit_price=Decimal("5.99"),
        )

        inserted = purchase_repo.add_if_absent(purchase)

        assert inserted is True

    def test_add_if_absent_skips_duplicate(self, session):
        purchase_repo = SqlAlchemyBibliothequePurchaseRepository(session)
        product_id = uuid4()
        now = datetime.now(timezone.utc)

        purchase1 = LibraryPurchase(
            product_id=product_id,
            source_document_ref="TICKET-001",
            source_document_type="ticket",
            line_index=0,
            purchased_at=now,
            quantity=Decimal("10"),
            unit_price=Decimal("5.99"),
        )

        inserted1 = purchase_repo.add_if_absent(purchase1)
        assert inserted1 is True

        # Identical purchase should be skipped
        purchase2 = LibraryPurchase(
            product_id=product_id,
            source_document_ref="TICKET-001",
            source_document_type="ticket",
            line_index=0,
            purchased_at=now,
            quantity=Decimal("10"),
            unit_price=Decimal("5.99"),
        )

        inserted2 = purchase_repo.add_if_absent(purchase2)
        assert inserted2 is False

    def test_add_if_absent_duplicate_key_is_product_doc_line(self, session):
        """Idempotency key is (product_id, source_document_ref, line_index)."""
        purchase_repo = SqlAlchemyBibliothequePurchaseRepository(session)
        product_id = uuid4()
        now = datetime.now(timezone.utc)

        purchase1 = LibraryPurchase(
            product_id=product_id,
            source_document_ref="TICKET-001",
            source_document_type="ticket",
            line_index=0,
            purchased_at=now,
            quantity=Decimal("10"),
            unit_price=Decimal("5.99"),
        )
        purchase_repo.add_if_absent(purchase1)

        # Different quantity/price but same (product, doc, line) → duplicate
        purchase2 = LibraryPurchase(
            product_id=product_id,
            source_document_ref="TICKET-001",
            source_document_type="ticket",
            line_index=0,
            purchased_at=now,
            quantity=Decimal("20"),
            unit_price=Decimal("6.99"),
        )

        inserted2 = purchase_repo.add_if_absent(purchase2)
        assert inserted2 is False

    def test_add_if_absent_different_line_index_is_new(self, session):
        """Different line_index should be treated as new purchase."""
        purchase_repo = SqlAlchemyBibliothequePurchaseRepository(session)
        product_id = uuid4()
        now = datetime.now(timezone.utc)

        purchase1 = LibraryPurchase(
            product_id=product_id,
            source_document_ref="TICKET-001",
            source_document_type="ticket",
            line_index=0,
            purchased_at=now,
            quantity=Decimal("10"),
            unit_price=Decimal("5.99"),
        )
        purchase_repo.add_if_absent(purchase1)

        # Same ticket, different line_index → new purchase
        purchase2 = LibraryPurchase(
            product_id=product_id,
            source_document_ref="TICKET-001",
            source_document_type="ticket",
            line_index=1,
            purchased_at=now,
            quantity=Decimal("20"),
            unit_price=Decimal("6.99"),
        )

        inserted2 = purchase_repo.add_if_absent(purchase2)
        assert inserted2 is True

    def test_add_if_absent_different_product_is_new(self, session):
        """Different product_id should be treated as new purchase."""
        purchase_repo = SqlAlchemyBibliothequePurchaseRepository(session)
        product1_id = uuid4()
        product2_id = uuid4()
        now = datetime.now(timezone.utc)

        purchase1 = LibraryPurchase(
            product_id=product1_id,
            source_document_ref="TICKET-001",
            source_document_type="ticket",
            line_index=0,
            purchased_at=now,
            quantity=Decimal("10"),
            unit_price=Decimal("5.99"),
        )
        purchase_repo.add_if_absent(purchase1)

        # Different product, same ticket/line → new purchase
        purchase2 = LibraryPurchase(
            product_id=product2_id,
            source_document_ref="TICKET-001",
            source_document_type="ticket",
            line_index=0,
            purchased_at=now,
            quantity=Decimal("10"),
            unit_price=Decimal("5.99"),
        )

        inserted2 = purchase_repo.add_if_absent(purchase2)
        assert inserted2 is True

    def test_list_by_product(self, session):
        purchase_repo = SqlAlchemyBibliothequePurchaseRepository(session)
        product_id = uuid4()
        now = datetime.now(timezone.utc)

        purchases = [
            LibraryPurchase(
                product_id=product_id,
                source_document_ref="TICKET-001",
                source_document_type="ticket",
                line_index=0,
                purchased_at=now,
                quantity=Decimal("10"),
                unit_price=Decimal("5.99"),
            ),
            LibraryPurchase(
                product_id=product_id,
                source_document_ref="TICKET-002",
                source_document_type="ticket",
                line_index=0,
                purchased_at=now + timedelta(days=1),
                quantity=Decimal("5"),
                unit_price=Decimal("6.99"),
            ),
        ]

        for p in purchases:
            purchase_repo.add_if_absent(p)

        results = purchase_repo.list_by_product(product_id)

        assert len(results) == 2

    def test_list_by_product_ordered_by_purchased_at_desc(self, session):
        """Purchases should be ordered by purchased_at DESC (newest first)."""
        purchase_repo = SqlAlchemyBibliothequePurchaseRepository(session)
        product_id = uuid4()
        now = datetime.now(timezone.utc)

        p1_time = now.replace(microsecond=1000)
        p2_time = (now + timedelta(days=1)).replace(microsecond=2000)
        p3_time = (now - timedelta(days=1)).replace(microsecond=3000)

        p1 = LibraryPurchase(
            product_id=product_id,
            source_document_ref="TICKET-001",
            source_document_type="ticket",
            line_index=0,
            purchased_at=p1_time,
            quantity=Decimal("10"),
            unit_price=Decimal("5.99"),
        )
        p2 = LibraryPurchase(
            product_id=product_id,
            source_document_ref="TICKET-002",
            source_document_type="ticket",
            line_index=0,
            purchased_at=p2_time,
            quantity=Decimal("5"),
            unit_price=Decimal("6.99"),
        )
        p3 = LibraryPurchase(
            product_id=product_id,
            source_document_ref="TICKET-003",
            source_document_type="ticket",
            line_index=0,
            purchased_at=p3_time,
            quantity=Decimal("20"),
            unit_price=Decimal("4.99"),
        )

        for p in [p1, p2, p3]:
            purchase_repo.add_if_absent(p)

        results = purchase_repo.list_by_product(product_id)

        # Should be DESC (newest first) — compare microseconds to handle naive/aware timezone differences
        assert results[0].purchased_at.replace(tzinfo=None) == p2_time.replace(tzinfo=None)
        assert results[1].purchased_at.replace(tzinfo=None) == p1_time.replace(tzinfo=None)
        assert results[2].purchased_at.replace(tzinfo=None) == p3_time.replace(tzinfo=None)
