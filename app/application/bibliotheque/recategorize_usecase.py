"""RecategorizeUseCase — bulk reassign product categories (curated override).

Category is the one product field the import flow deliberately will NOT
overwrite (with_enrichment fills empty slots only, to protect curated data).
This use-case is the explicit, permissioned path to re-bucket an existing
library into a canonical supplier taxonomy. It touches ONLY the category
field — purchase rows and aggregates are never modified.
"""

from __future__ import annotations

import logging
from uuid import UUID

from app.application.bibliotheque.dtos import RecategorizeItemDTO, RecategorizeResultDTO
from app.application.bibliotheque.exceptions import (
    CompanyAccessDeniedError,
    InsufficientPermissionError,
    SupplierNotFoundError,
)
from app.application.bibliotheque.ports import (
    ICompanyMembershipReader,
    ICompanyPermissionChecker,
    ILibraryProductRepository,
    ISupplierRepository,
    TransactionalSessionPort,
)

_log = logging.getLogger(__name__)

_MANAGE_PERMISSION = "bibliotheque:manage"


class RecategorizeUseCase:
    """Bulk-reassign categories for a supplier's products within a company.

    Authorization: requester must be a company member AND hold the
    'bibliotheque:manage' permission (same gate as import).
    """

    def __init__(
        self,
        supplier_repo: ISupplierRepository,
        product_repo: ILibraryProductRepository,
        membership_reader: ICompanyMembershipReader,
        permission_checker: ICompanyPermissionChecker,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._supplier_repo = supplier_repo
        self._product_repo = product_repo
        self._membership = membership_reader
        self._permission_checker = permission_checker
        self._db = db_session

    def execute(
        self,
        *,
        requester_id: UUID,
        company_id: UUID,
        supplier_slug: str,
        items: list[RecategorizeItemDTO],
    ) -> RecategorizeResultDTO:
        """Apply category reassignments. Returns counts of updated/unchanged/not_found."""
        # Authorization: membership + named permission (defense-in-depth)
        if not self._membership.is_member(requester_id, company_id):
            raise CompanyAccessDeniedError(f"User {requester_id} is not a member of company {company_id}.")
        if not self._permission_checker.has_permission(requester_id, _MANAGE_PERMISSION):
            raise InsufficientPermissionError(f"User {requester_id} lacks '{_MANAGE_PERMISSION}' permission.")

        supplier = self._supplier_repo.find_by_slug(company_id, supplier_slug)
        if supplier is None:
            raise SupplierNotFoundError(f"No supplier '{supplier_slug}' for company {company_id}.")

        updated = 0
        unchanged = 0
        not_found = 0

        for item in items:
            product = self._product_repo.find_by_reference(company_id, supplier.id, item.supplier_reference)
            if product is None:
                not_found += 1
                continue
            if product.category == item.category:
                unchanged += 1
                continue
            self._product_repo.upsert(product.with_category(item.category))
            updated += 1

        self._db.commit()
        return RecategorizeResultDTO(updated=updated, unchanged=unchanged, not_found=not_found)
