"""ImportPurchasesUseCase — idempotent bulk import of purchase records.

Idempotency is the highest-value invariant: re-posting the same payload must
produce exactly 0 new purchase rows and leave all product aggregates unchanged.

The guarantee is enforced at the repository layer via add_if_absent, which
uses a pre-check + SAVEPOINT insert so that duplicate rows are silently
skipped without aborting the outer transaction. Aggregate updates
(with_purchase_applied) are only called when add_if_absent returns True.
"""

from __future__ import annotations

import logging
from datetime import timezone
from typing import Optional
from uuid import UUID

from app.application.bibliotheque.dtos import ImportRecordDTO, ImportResultDTO
from app.application.bibliotheque.exceptions import CompanyAccessDeniedError, InsufficientPermissionError
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
from app.domain.value_objects.library_category import normalize_category
from app.domain.value_objects.library_purchase import LibraryPurchase

_log = logging.getLogger(__name__)

_MANAGE_PERMISSION = "bibliotheque:manage"
_BATCH_SIZE = 500  # bound memory usage for large imports


class ImportPurchasesUseCase:
    """Bulk-import purchase records for a company's product library.

    Authorization: requester must be a company member AND hold
    the 'bibliotheque:manage' permission.
    """

    def __init__(
        self,
        supplier_repo: ISupplierRepository,
        product_repo: ILibraryProductRepository,
        purchase_repo: ILibraryPurchaseRepository,
        membership_reader: ICompanyMembershipReader,
        permission_checker: ICompanyPermissionChecker,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._supplier_repo = supplier_repo
        self._product_repo = product_repo
        self._purchase_repo = purchase_repo
        self._membership = membership_reader
        self._permission_checker = permission_checker
        self._db = db_session

    def execute(
        self,
        *,
        requester_id: UUID,
        company_id: UUID,
        supplier_name: str,
        supplier_slug: str,
        supplier_website_url: Optional[str] = None,
        supplier_product_url_template: Optional[str] = None,
        records: list[ImportRecordDTO],
    ) -> ImportResultDTO:
        """Import purchase records idempotently.

        Returns a summary of created/updated products and added/skipped purchases.
        """
        # Authorization: membership + named permission
        if not self._membership.is_member(requester_id, company_id):
            raise CompanyAccessDeniedError(f"User {requester_id} is not a member of company {company_id}.")
        if not self._permission_checker.has_permission(requester_id, _MANAGE_PERMISSION):
            raise InsufficientPermissionError(f"User {requester_id} lacks '{_MANAGE_PERMISSION}' permission.")

        # Step 1: resolve supplier (idempotent get_or_create)
        supplier_template = Supplier.create(
            company_id=company_id,
            name=supplier_name,
            slug=supplier_slug,
            website_url=supplier_website_url,
            product_url_template=supplier_product_url_template,
        )
        supplier = self._supplier_repo.get_or_create(supplier_template)

        created = 0
        updated = 0
        purchases_added = 0
        skipped = 0

        # Process in batches to bound memory usage on large payloads
        for i in range(0, max(1, len(records)), _BATCH_SIZE):
            batch = records[i : i + _BATCH_SIZE]
            _c, _u, _pa, _sk = self._process_batch(
                company_id=company_id,
                supplier_id=supplier.id,
                batch=batch,
            )
            created += _c
            updated += _u
            purchases_added += _pa
            skipped += _sk

        self._db.commit()
        return ImportResultDTO(
            created=created,
            updated=updated,
            purchases_added=purchases_added,
            skipped=skipped,
        )

    def _process_batch(
        self,
        *,
        company_id: UUID,
        supplier_id: UUID,
        batch: list[ImportRecordDTO],
    ) -> tuple[int, int, int, int]:
        """Process one batch of records. Returns (created, updated, added, skipped)."""
        created = updated = purchases_added = skipped = 0

        for rec in batch:
            # Normalise category once per record before any DB access.
            # Free-text from the import source (e.g. Leroy Merlin) is mapped to
            # a canonical slug here; the schema intentionally leaves the transport
            # field as free-text so the use-case is the single normalisation point.
            cat = normalize_category(rec.category)

            # Step 2a: find or create product by supplier reference
            product = self._product_repo.find_by_reference(company_id, supplier_id, rec.supplier_reference)
            is_new = product is None

            if is_new:
                product = LibraryProduct.create(
                    company_id=company_id,
                    supplier_id=supplier_id,
                    supplier_reference=rec.supplier_reference,
                    name=rec.product_name,
                    description=rec.description,
                    size=rec.size,
                    category=cat,
                    product_url=rec.product_url,
                )
                product = self._product_repo.upsert(product)
                created += 1
            else:
                # Apply enrichment to empty fields only (never overwrite non-null)
                enriched = product.with_enrichment(  # type: ignore[union-attr]
                    name=rec.product_name,
                    description=rec.description,
                    size=rec.size,
                    category=cat,
                    product_url=rec.product_url,
                )
                if enriched != product:
                    product = self._product_repo.upsert(enriched)
                    updated += 1

            # Step 2b: attempt idempotent purchase insert
            # Coerce naive datetimes to UTC so comparisons with the timezone-aware
            # last_purchased_at column never raise "can't compare offset-naive and
            # offset-aware datetimes".  Clients that omit the 'Z' suffix send naive
            # datetimes; treating them as UTC is safe because all purchase timestamps
            # in this system are expected to be UTC.
            purchased_at = rec.purchased_at
            if purchased_at.tzinfo is None:
                purchased_at = purchased_at.replace(tzinfo=timezone.utc)

            purchase = LibraryPurchase(
                product_id=product.id,  # type: ignore[union-attr]
                source_document_ref=rec.source_document_ref,
                source_document_type=rec.source_document_type,
                line_index=rec.line_index,
                purchased_at=purchased_at,
                quantity=rec.quantity,
                unit_price=rec.unit_price,
            )
            inserted = self._purchase_repo.add_if_absent(purchase)

            if inserted:
                purchases_added += 1
                # Step 2c: apply aggregates only when a purchase was actually inserted
                # to preserve idempotency (no double-counting on re-import).
                locked_product = self._product_repo.find_by_id_for_update(product.id)  # type: ignore[union-attr]
                if locked_product is not None:
                    updated_product = locked_product.with_purchase_applied(
                        qty=rec.quantity,
                        unit_price=rec.unit_price,
                        purchased_at=purchased_at,
                    )
                    self._product_repo.upsert(updated_product)
                    if not is_new:
                        updated += 1
            else:
                skipped += 1

        return created, updated, purchases_added, skipped
