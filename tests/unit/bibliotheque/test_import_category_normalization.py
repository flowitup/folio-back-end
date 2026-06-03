"""Unit tests for category normalization in import use-case and PATCH schema.

Covers:
- Import use-case normalizes free-text category to slug on create
- Import use-case normalizes category on enrichment (fill-once)
- Enrichment does NOT overwrite an existing non-null slug
- Null category stays null through import
- UpdateProductSchema rejects non-slug values (422)
- UpdateProductSchema accepts all valid slugs including "autre"
- UpdateProductSchema accepts explicit null (clear)
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.bibliotheque.dtos import ImportRecordDTO
from app.application.bibliotheque.import_purchases_usecase import ImportPurchasesUseCase
from app.application.bibliotheque.ports import (
    ICompanyMembershipReader,
    ICompanyPermissionChecker,
    ILibraryProductRepository,
    ILibraryPurchaseRepository,
    ISupplierRepository,
    TransactionalSessionPort,
)
from app.domain.entities.library_product import LibraryProduct
from app.domain.entities.supplier import Supplier
from app.domain.value_objects.library_category import LIBRARY_CATEGORY_SLUGS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_supplier(company_id=None) -> Supplier:
    return Supplier(
        id=uuid4(),
        company_id=company_id or uuid4(),
        name="Test Supplier",
        slug="test-supplier",
        website_url=None,
        logo_url=None,
        product_url_template=None,
        created_at=datetime.now(timezone.utc),
    )


def _make_product(company_id=None, supplier_id=None, category: Optional[str] = None) -> LibraryProduct:
    return LibraryProduct.create(
        company_id=company_id or uuid4(),
        supplier_id=supplier_id or uuid4(),
        supplier_reference="REF-001",
        name="Test Product",
        category=category,
    )


def _make_record(category: Optional[str] = None, sku: str = "REF-001") -> ImportRecordDTO:
    return ImportRecordDTO(
        supplier_reference=sku,
        product_name="Test Product",
        quantity=Decimal("1.0"),
        unit_price=Decimal("10.00"),
        purchased_at=datetime.now(timezone.utc),
        source_document_ref="DOC-001",
        source_document_type="ticket",
        line_index=0,
        category=category,
        description=None,
        size=None,
        product_url=None,
    )


def _make_use_case(
    supplier_repo=None,
    product_repo=None,
    purchase_repo=None,
    membership_reader=None,
    permission_checker=None,
    db_session=None,
) -> ImportPurchasesUseCase:
    """Build an ImportPurchasesUseCase with sensible mock defaults."""
    company_id = uuid4()
    requester_id = uuid4()

    if supplier_repo is None:
        supplier_repo = MagicMock(spec=ISupplierRepository)
        supplier = _make_supplier(company_id)
        supplier_repo.get_or_create.return_value = supplier

    if membership_reader is None:
        membership_reader = MagicMock(spec=ICompanyMembershipReader)
        membership_reader.is_member.return_value = True

    if permission_checker is None:
        permission_checker = MagicMock(spec=ICompanyPermissionChecker)
        permission_checker.has_permission.return_value = True

    if db_session is None:
        db_session = MagicMock(spec=TransactionalSessionPort)

    if purchase_repo is None:
        purchase_repo = MagicMock(spec=ILibraryPurchaseRepository)
        purchase_repo.add_if_absent.return_value = False  # skip purchase aggregates by default

    if product_repo is None:
        product_repo = MagicMock(spec=ILibraryProductRepository)

    return (
        ImportPurchasesUseCase(
            supplier_repo=supplier_repo,
            product_repo=product_repo,
            purchase_repo=purchase_repo,
            membership_reader=membership_reader,
            permission_checker=permission_checker,
            db_session=db_session,
        ),
        company_id,
        requester_id,
    )


# ---------------------------------------------------------------------------
# Test: import normalizes category on product CREATE
# ---------------------------------------------------------------------------


class TestImportNormalizesCategoryOnCreate:
    def test_free_text_category_normalized_on_create(self) -> None:
        """Import with raw "Plomberie" must store "plomberie" on new product."""
        use_case, company_id, requester_id = _make_use_case()

        created_products = []

        def upsert_capture(product):
            created_products.append(product)
            return product

        use_case._product_repo.find_by_reference.return_value = None  # new product
        use_case._product_repo.upsert.side_effect = upsert_capture

        use_case._process_batch(
            company_id=company_id,
            supplier_id=uuid4(),
            batch=[_make_record(category="Plomberie")],
        )

        assert len(created_products) == 1
        assert created_products[0].category == "plomberie"

    def test_null_category_stays_null_on_create(self) -> None:
        """Import with no category must store None (not 'autre')."""
        use_case, company_id, _ = _make_use_case()
        created_products = []

        def upsert_capture(product):
            created_products.append(product)
            return product

        use_case._product_repo.find_by_reference.return_value = None
        use_case._product_repo.upsert.side_effect = upsert_capture

        use_case._process_batch(
            company_id=company_id,
            supplier_id=uuid4(),
            batch=[_make_record(category=None)],
        )

        assert len(created_products) == 1
        assert created_products[0].category is None

    def test_unmappable_category_becomes_autre(self) -> None:
        """Import with random text must store "autre"."""
        use_case, company_id, _ = _make_use_case()
        created_products = []

        def upsert_capture(product):
            created_products.append(product)
            return product

        use_case._product_repo.find_by_reference.return_value = None
        use_case._product_repo.upsert.side_effect = upsert_capture

        use_case._process_batch(
            company_id=company_id,
            supplier_id=uuid4(),
            batch=[_make_record(category="Café gourmand")],
        )

        assert created_products[0].category == "autre"

    def test_already_slug_stays_as_slug(self) -> None:
        """Import with canonical slug must stay as that slug (idempotent)."""
        use_case, company_id, _ = _make_use_case()
        created_products = []

        def upsert_capture(product):
            created_products.append(product)
            return product

        use_case._product_repo.find_by_reference.return_value = None
        use_case._product_repo.upsert.side_effect = upsert_capture

        use_case._process_batch(
            company_id=company_id,
            supplier_id=uuid4(),
            batch=[_make_record(category="plomberie")],
        )

        assert created_products[0].category == "plomberie"


# ---------------------------------------------------------------------------
# Test: import normalizes category on enrichment (fill-once)
# ---------------------------------------------------------------------------


class TestImportNormalizesCategoryOnEnrichment:
    def test_free_text_category_normalized_on_enrichment(self) -> None:
        """Re-import with raw category fills empty slot with slug."""
        company_id = uuid4()
        supplier_id = uuid4()
        existing_product = _make_product(company_id=company_id, supplier_id=supplier_id, category=None)

        use_case, _, _ = _make_use_case()
        enriched_products = []

        def upsert_capture(product):
            enriched_products.append(product)
            return product

        use_case._product_repo.find_by_reference.return_value = existing_product
        use_case._product_repo.upsert.side_effect = upsert_capture

        use_case._process_batch(
            company_id=company_id,
            supplier_id=supplier_id,
            batch=[_make_record(category="Plomberie")],
        )

        # Should have been enriched with the normalized slug
        assert len(enriched_products) == 1
        assert enriched_products[0].category == "plomberie"

    def test_enrichment_does_not_overwrite_existing_non_null_slug(self) -> None:
        """Re-import must NOT overwrite an existing non-null category slug."""
        company_id = uuid4()
        supplier_id = uuid4()
        # Product already has a category slug from a previous import
        existing_product = _make_product(company_id=company_id, supplier_id=supplier_id, category="outillage")

        use_case, _, _ = _make_use_case()
        enriched_products = []

        def upsert_capture(product):
            enriched_products.append(product)
            return product

        use_case._product_repo.find_by_reference.return_value = existing_product
        use_case._product_repo.upsert.side_effect = upsert_capture

        # Re-import with a different category — must NOT overwrite
        use_case._process_batch(
            company_id=company_id,
            supplier_id=supplier_id,
            batch=[_make_record(category="Plomberie")],
        )

        # No upsert called (product unchanged) OR category remains "outillage"
        if enriched_products:
            # If an upsert happened (e.g. name enrichment), category must be unchanged
            assert enriched_products[0].category == "outillage"
        else:
            # No update — that's fine too
            assert existing_product.category == "outillage"

    def test_null_import_does_not_overwrite_existing_slug(self) -> None:
        """Re-import with null category must not clear an existing slug."""
        company_id = uuid4()
        supplier_id = uuid4()
        existing_product = _make_product(company_id=company_id, supplier_id=supplier_id, category="plomberie")

        use_case, _, _ = _make_use_case()
        enriched_products = []

        def upsert_capture(product):
            enriched_products.append(product)
            return product

        use_case._product_repo.find_by_reference.return_value = existing_product
        use_case._product_repo.upsert.side_effect = upsert_capture

        use_case._process_batch(
            company_id=company_id,
            supplier_id=supplier_id,
            batch=[_make_record(category=None)],
        )

        if enriched_products:
            assert enriched_products[0].category == "plomberie"
        else:
            assert existing_product.category == "plomberie"


# ---------------------------------------------------------------------------
# Test: UpdateProductSchema category validation
# ---------------------------------------------------------------------------


class TestUpdateProductSchemaCategory:
    def _parse(self, payload: dict):
        from app.api.v1.bibliotheque.schemas import UpdateProductSchema

        return UpdateProductSchema.model_validate(payload)

    def test_valid_slug_accepted(self) -> None:
        schema = self._parse({"category": "plomberie"})
        assert schema.category == "plomberie"

    def test_autre_slug_accepted(self) -> None:
        schema = self._parse({"category": "autre"})
        assert schema.category == "autre"

    @pytest.mark.parametrize("slug", LIBRARY_CATEGORY_SLUGS)
    def test_all_canonical_slugs_accepted(self, slug: str) -> None:
        schema = self._parse({"category": slug})
        assert schema.category == slug

    def test_explicit_null_accepted(self) -> None:
        schema = self._parse({"category": None})
        assert schema.category is None

    def test_omitted_category_is_none(self) -> None:
        schema = self._parse({})
        assert schema.category is None

    def test_free_text_rejected_with_validation_error(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            self._parse({"category": "Plomberie"})  # capitalised — not a valid slug
        assert "category" in str(exc_info.value).lower() or "slug" in str(exc_info.value).lower()

    def test_capitalised_slug_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._parse({"category": "OUTILLAGE"})

    def test_random_string_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._parse({"category": "bogus"})


# ---------------------------------------------------------------------------
# Test: backfill migration mapping logic (unit-level, no DB needed)
# ---------------------------------------------------------------------------


class TestBackfillMigrationLogic:
    """Validate the normalize_category logic that the migration applies.

    These tests directly exercise the pure function with the kind of raw
    data that would exist in the DB before migration.
    """

    @pytest.mark.parametrize(
        "raw, expected_slug",
        [
            # Already-normalised slugs should survive unchanged
            ("plomberie", "plomberie"),
            ("outillage", "outillage"),
            ("autre", "autre"),
            # Typical LM free-text imports
            ("Plomberie", "plomberie"),
            ("Outillage", "outillage"),
            ("Chauffage", "chauffage_clim_ventilation"),
            ("Peinture", "revetement_sol_mur_peinture"),
            ("Carrelage", "revetement_sol_mur_peinture"),
            # Unmappable values become "autre"
            ("Informatique", "autre"),
            ("Divers", "autre"),
            # Accented raw values from LM import
            ("Décoration", "decoration"),
            ("Matériaux", "materiaux_construction"),
            ("Électricité", "electricite_domotique"),
        ],
    )
    def test_raw_category_maps_to_expected_slug(self, raw: str, expected_slug: str) -> None:
        from app.domain.value_objects.library_category import normalize_category

        assert normalize_category(raw) == expected_slug

    def test_null_stays_null(self) -> None:
        from app.domain.value_objects.library_category import normalize_category

        assert normalize_category(None) is None

    def test_idempotent_on_already_normalised_db_rows(self) -> None:
        """Simulate re-running migration on already-normalised rows — must be no-op."""
        from app.domain.value_objects.library_category import normalize_category

        for slug in LIBRARY_CATEGORY_SLUGS:
            assert normalize_category(slug) == slug, f"Re-run not idempotent for slug {slug!r}"
