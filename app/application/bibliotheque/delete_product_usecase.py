"""DeleteProductUseCase — remove a product and its purchases from the company library.

Auth order: find product → membership → bibliotheque:manage → repo.delete → commit
→ best-effort image cleanup (swallowed, never fails the request).
"""

from __future__ import annotations

import logging
from uuid import UUID

from app.application.bibliotheque.exceptions import (
    CompanyAccessDeniedError,
    InsufficientPermissionError,
    ProductNotFoundError,
)
from app.application.bibliotheque.ports import (
    ICompanyMembershipReader,
    ICompanyPermissionChecker,
    ILibraryProductRepository,
    IProductImageStorage,
    TransactionalSessionPort,
)

_log = logging.getLogger(__name__)

_MANAGE_PERMISSION = "bibliotheque:manage"


class DeleteProductUseCase:
    """Delete a library product (and its purchases). Requires membership + bibliotheque:manage."""

    def __init__(
        self,
        product_repo: ILibraryProductRepository,
        image_storage: IProductImageStorage,
        membership_reader: ICompanyMembershipReader,
        permission_checker: ICompanyPermissionChecker,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._product_repo = product_repo
        self._image_storage = image_storage
        self._membership = membership_reader
        self._permission_checker = permission_checker
        self._db = db_session

    def execute(self, *, requester_id: UUID, product_id: UUID) -> None:
        """Delete the product identified by product_id.

        Purchases are deleted before the product row (explicit cascade).
        Image bytes are cleaned up best-effort after the transaction commits —
        a storage failure never surfaces to the caller.
        """
        # 1. Load product (authoritative company_id comes from the DB row).
        product = self._product_repo.find_by_id(product_id)
        if product is None:
            raise ProductNotFoundError(f"Product {product_id} not found.")

        # 2. Membership check.
        if not self._membership.is_member(requester_id, product.company_id):
            raise CompanyAccessDeniedError(f"User {requester_id} is not a member of company {product.company_id}.")

        # 3. Permission check.
        if not self._permission_checker.has_permission(requester_id, _MANAGE_PERMISSION):
            raise InsufficientPermissionError(f"User {requester_id} lacks '{_MANAGE_PERMISSION}' permission.")

        # 4. Capture image key before deletion (needed for post-commit cleanup).
        image_key = product.image_storage_key

        # 5. Delete purchases + product row.
        self._product_repo.delete(product_id)

        # 6. Commit transaction — the row is gone from this point forward.
        self._db.commit()

        # 7. Best-effort image cleanup — never fail the request on storage errors.
        if image_key:
            try:
                self._image_storage.delete(image_key)
            except Exception:
                _log.warning(
                    "Failed to delete image for product %s (key=%s); storage cleanup skipped.",
                    product_id,
                    image_key,
                )
