"""GetProductImageUseCase — return a presigned URL or stream for a product image."""

from __future__ import annotations

from uuid import UUID

from app.application.bibliotheque.exceptions import CompanyAccessDeniedError, ProductNotFoundError
from app.application.bibliotheque.ports import (
    ICompanyMembershipReader,
    ILibraryProductRepository,
    IProductImageStorage,
)


class GetProductImageUseCase:
    """Return a presigned GET URL for the product image.

    Any company member may fetch a product image.
    Raises ProductNotFoundError if the product or its image does not exist.
    """

    def __init__(
        self,
        product_repo: ILibraryProductRepository,
        image_storage: IProductImageStorage,
        membership_reader: ICompanyMembershipReader,
    ) -> None:
        self._product_repo = product_repo
        self._image_storage = image_storage
        self._membership = membership_reader

    def execute(self, *, requester_id: UUID, product_id: UUID) -> str:
        """Return a presigned GET URL for the product's image.

        Raises:
            ProductNotFoundError: product not found or has no image.
            CompanyAccessDeniedError: requester is not a company member.
        """
        product = self._product_repo.find_by_id(product_id)
        if product is None:
            raise ProductNotFoundError(f"Product {product_id} not found.")

        if not self._membership.is_member(requester_id, product.company_id):
            raise CompanyAccessDeniedError(f"User {requester_id} is not a member of company {product.company_id}.")

        if product.image_storage_key is None:
            raise ProductNotFoundError(f"Product {product_id} has no image.")

        return self._image_storage.presigned_get_url(product.image_storage_key)
