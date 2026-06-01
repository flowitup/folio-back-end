"""UpdateProductUseCase — edit an existing library product's editable fields.

Curated edit path: overwrites whatever the client sends (name, category,
description, size, product_url), including clearing to null. Fields not sent
are left unchanged. Image bytes are edited via the dedicated image endpoints.
Purchase rows and aggregates are never touched.
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
    TransactionalSessionPort,
)
from app.domain.entities.library_product import LibraryProduct

_log = logging.getLogger(__name__)

_MANAGE_PERMISSION = "bibliotheque:manage"

# Re-export the entity sentinel so callers (routes) can express "field omitted".
UNSET = LibraryProduct._UNSET


class UpdateProductUseCase:
    """Edit a single product's metadata. Requires membership + bibliotheque:manage."""

    def __init__(
        self,
        product_repo: ILibraryProductRepository,
        membership_reader: ICompanyMembershipReader,
        permission_checker: ICompanyPermissionChecker,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._product_repo = product_repo
        self._membership = membership_reader
        self._permission_checker = permission_checker
        self._db = db_session

    def execute(
        self,
        *,
        requester_id: UUID,
        product_id: UUID,
        name: object = UNSET,
        category: object = UNSET,
        description: object = UNSET,
        size: object = UNSET,
        product_url: object = UNSET,
    ) -> LibraryProduct:
        """Apply edits to a product and return the persisted entity."""
        product = self._product_repo.find_by_id(product_id)
        if product is None:
            raise ProductNotFoundError(f"Product {product_id} not found.")

        # Authorization: membership in the product's company + named permission.
        if not self._membership.is_member(requester_id, product.company_id):
            raise CompanyAccessDeniedError(f"User {requester_id} is not a member of company {product.company_id}.")
        if not self._permission_checker.has_permission(requester_id, _MANAGE_PERMISSION):
            raise InsufficientPermissionError(f"User {requester_id} lacks '{_MANAGE_PERMISSION}' permission.")

        updated = product.with_updates(
            name=name,
            category=category,
            description=description,
            size=size,
            product_url=product_url,
        )
        if updated == product:
            return product
        persisted = self._product_repo.upsert(updated)
        self._db.commit()
        return persisted
