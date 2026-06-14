"""Port interfaces (Protocols) for the bibliotheque application layer."""

from __future__ import annotations

from typing import Optional, Protocol
from uuid import UUID

from app.domain.entities.library_product import LibraryProduct
from app.domain.entities.supplier import Supplier
from app.domain.value_objects.library_purchase import LibraryPurchase


class ISupplierRepository(Protocol):
    """Persistence contract for the Supplier aggregate."""

    def get_or_create(self, supplier: Supplier) -> Supplier:
        """Return existing supplier for (company_id, slug), or insert and return new one."""
        ...

    def list_by_company(self, company_id: UUID) -> list[Supplier]:
        """Return all suppliers for a company ordered by name."""
        ...

    def find_by_id(self, supplier_id: UUID) -> Optional[Supplier]:
        """Return supplier by UUID, or None."""
        ...

    def find_by_slug(self, company_id: UUID, slug: str) -> Optional[Supplier]:
        """Return supplier by (company_id, slug), or None."""
        ...


class ILibraryProductRepository(Protocol):
    """Persistence contract for the LibraryProduct aggregate."""

    def upsert(self, product: LibraryProduct) -> LibraryProduct:
        """Insert or update a product. Returns the persisted instance."""
        ...

    def find_by_reference(self, company_id: UUID, supplier_id: UUID, reference: str) -> Optional[LibraryProduct]:
        """Return product by (company_id, supplier_id, supplier_reference), or None."""
        ...

    def find_by_id(self, product_id: UUID) -> Optional[LibraryProduct]:
        """Return product by UUID, or None."""
        ...

    def find_by_id_for_update(self, product_id: UUID) -> Optional[LibraryProduct]:
        """Return product by UUID with SELECT FOR UPDATE lock, or None.

        Used when applying purchase aggregates to serialize concurrent imports
        on the same product row and prevent double-counting.
        """
        ...

    def list(
        self,
        company_id: UUID,
        *,
        supplier_id: Optional[UUID] = None,
        category: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[LibraryProduct], int]:
        """Return paginated products and total count matching the filters."""
        ...

    def distinct_categories(self, company_id: UUID) -> list[str]:
        """Return sorted distinct non-null category values for the company."""
        ...

    def add(self, product: LibraryProduct) -> LibraryProduct:
        """Insert a new product row. Returns the persisted instance.

        IntegrityError propagates on duplicate (company_id, supplier_id, supplier_reference).
        Use-case maps that to ProductAlreadyExistsError. Do NOT swallow here.
        """
        ...

    def delete(self, product_id: UUID) -> bool:
        """Delete a product and its purchases. Returns True if a row was deleted, False if not found."""
        ...


class ILibraryPurchaseRepository(Protocol):
    """Persistence contract for LibraryPurchase records."""

    def add_if_absent(self, purchase: LibraryPurchase) -> bool:
        """Insert purchase if (product_id, source_document_ref, line_index) is new.

        Returns True when a row was actually inserted. Returns False when the
        triple already exists — the caller must NOT apply aggregate updates in
        that case to keep re-import idempotent.
        """
        ...

    def list_by_product(self, product_id: UUID) -> list[LibraryPurchase]:
        """Return all purchases for a product ordered by purchased_at DESC."""
        ...


class IProductImageStorage(Protocol):
    """Storage contract for product images (S3/MinIO-backed)."""

    def put(self, key: str, fileobj: object, content_type: str) -> None:
        """Upload image bytes under the given key."""
        ...

    def get_stream(self, key: str) -> tuple[object, int, str]:
        """Return (body_stream, content_length, content_type) for the given key."""
        ...

    def delete(self, key: str) -> None:
        """Delete the object at the given key (idempotent)."""
        ...


class ICompanyMembershipReader(Protocol):
    """Read-only company membership check."""

    def is_member(self, user_id: UUID, company_id: UUID) -> bool:
        """Return True if the user has a user_company_access row for company_id."""
        ...


class ICompanyPermissionChecker(Protocol):
    """Check named permissions against a user's global roles."""

    def has_permission(self, user_id: UUID, permission_name: str) -> bool:
        """Return True if the user holds the given permission via any assigned role."""
        ...


# Re-export from invitations to avoid duplication (same minimal session contract).
from app.application.invitations.ports import TransactionalSessionPort as TransactionalSessionPort  # noqa: E402,F401
