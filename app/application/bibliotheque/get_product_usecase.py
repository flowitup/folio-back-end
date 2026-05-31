"""GetProductUseCase — fetch one product with its purchase history."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.application.bibliotheque.dtos import LibraryProductResponse, LibraryPurchaseResponse
from app.application.bibliotheque.exceptions import CompanyAccessDeniedError, ProductNotFoundError
from app.application.bibliotheque.ports import (
    ICompanyMembershipReader,
    ILibraryProductRepository,
    ILibraryPurchaseRepository,
)


@dataclass(frozen=True)
class ProductDetailResponse:
    """Product detail with full purchase history."""

    product: LibraryProductResponse
    purchases: list[LibraryPurchaseResponse]


class GetProductUseCase:
    """Fetch one product by id plus its purchase history.

    Any company member may read product details.
    """

    def __init__(
        self,
        product_repo: ILibraryProductRepository,
        purchase_repo: ILibraryPurchaseRepository,
        membership_reader: ICompanyMembershipReader,
    ) -> None:
        self._product_repo = product_repo
        self._purchase_repo = purchase_repo
        self._membership = membership_reader

    def execute(self, *, requester_id: UUID, product_id: UUID) -> ProductDetailResponse:
        product = self._product_repo.find_by_id(product_id)
        if product is None:
            raise ProductNotFoundError(f"Product {product_id} not found.")

        if not self._membership.is_member(requester_id, product.company_id):
            raise CompanyAccessDeniedError(f"User {requester_id} is not a member of company {product.company_id}.")

        purchases = self._purchase_repo.list_by_product(product_id)
        return ProductDetailResponse(
            product=LibraryProductResponse.from_entity(product),
            purchases=[LibraryPurchaseResponse.from_vo(p) for p in purchases],
        )
