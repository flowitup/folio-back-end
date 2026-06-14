"""CreateProductUseCase — manually add a new product to the company library.

Resolves or creates the supplier inline, auto-generates a reference when none
is provided, and persists a new product row. Duplicate (company, supplier,
reference) triples surface as ProductAlreadyExistsError (409).
"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4
from typing import Optional

from sqlalchemy.exc import IntegrityError

from app.application.bibliotheque.exceptions import (
    CompanyAccessDeniedError,
    InsufficientPermissionError,
    InvalidProductInputError,
    ProductAlreadyExistsError,
    SupplierNotFoundError,
)
from app.application.bibliotheque.ports import (
    ICompanyMembershipReader,
    ICompanyPermissionChecker,
    ILibraryProductRepository,
    ISupplierRepository,
    TransactionalSessionPort,
)
from app.domain.entities.library_product import LibraryProduct
from app.domain.entities.supplier import Supplier
from app.domain.value_objects.supplier_slug import slugify

_log = logging.getLogger(__name__)

_MANAGE_PERMISSION = "bibliotheque:manage"


class CreateProductUseCase:
    """Create a new library product for a company. Requires membership + bibliotheque:manage."""

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
        name: str,
        supplier_id: Optional[UUID] = None,
        supplier_name: Optional[str] = None,
        supplier_website_url: Optional[str] = None,
        supplier_reference: Optional[str] = None,
        category: Optional[str] = None,
        description: Optional[str] = None,
        size: Optional[str] = None,
        product_url: Optional[str] = None,
    ) -> LibraryProduct:
        """Create and persist a new library product.

        Auth order: membership → bibliotheque:manage → resolve supplier → insert.
        """
        # 1. Membership check.
        if not self._membership.is_member(requester_id, company_id):
            raise CompanyAccessDeniedError(f"User {requester_id} is not a member of company {company_id}.")

        # 2. Permission check.
        if not self._permission_checker.has_permission(requester_id, _MANAGE_PERMISSION):
            raise InsufficientPermissionError(f"User {requester_id} lacks '{_MANAGE_PERMISSION}' permission.")

        # 3. Resolve supplier — exactly one of supplier_id / supplier_name must be set.
        #    The route schema enforces this too; this is defense-in-depth.
        if supplier_id is not None:
            s = self._supplier_repo.find_by_id(supplier_id)
            if s is None or s.company_id != company_id:
                raise SupplierNotFoundError(f"Supplier {supplier_id} not found in company {company_id}.")
        elif supplier_name is not None:
            s = self._supplier_repo.get_or_create(
                Supplier.create(
                    company_id=company_id,
                    name=supplier_name.strip(),
                    slug=slugify(supplier_name),
                    website_url=supplier_website_url,
                )
            )
        else:
            raise InvalidProductInputError("Provide exactly one of supplier_id or supplier_name.")

        # 4. Auto-generate reference when blank/omitted.
        ref = (supplier_reference or "").strip() or f"manual-{uuid4().hex[:12]}"

        # 5. Build domain entity.
        product = LibraryProduct.create(
            company_id=company_id,
            supplier_id=s.id,
            supplier_reference=ref,
            name=name.strip(),
            description=description,
            size=size,
            category=category,
            product_url=product_url,
        )

        # 6. Persist — wrap in a SAVEPOINT so an IntegrityError rolls back only
        #    the nested block, leaving the outer session alive for subsequent requests.
        try:
            with self._db.begin_nested():
                persisted = self._product_repo.add(product)
        except IntegrityError as exc:
            raise ProductAlreadyExistsError(
                f"A product with supplier reference '{ref}' already exists for this supplier."
            ) from exc

        # 7. Commit and return.
        self._db.commit()
        return persisted
