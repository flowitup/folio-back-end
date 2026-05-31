"""GetProductImageUseCase — stream a product image's bytes through the API."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.application.bibliotheque.exceptions import CompanyAccessDeniedError, ProductNotFoundError
from app.application.bibliotheque.ports import (
    ICompanyMembershipReader,
    ILibraryProductRepository,
    IProductImageStorage,
)


class GetProductImageUseCase:
    """Stream a product image's bytes back through the API.

    Any company member may fetch a product image. Bytes are streamed
    server-side (not via a presigned object-store URL) because the store
    endpoint is not browser-reachable; this mirrors invoice attachment serving.
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

    def execute(self, *, requester_id: UUID, product_id: UUID) -> tuple[Any, int, str]:
        """Return (body_stream, content_length, content_type) for the image.

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

        return self._image_storage.get_stream(product.image_storage_key)
