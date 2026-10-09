"""UploadProductImageUseCase — store image bytes for a product.

Ingestion pushes pre-fetched image bytes to this endpoint; the backend
never scrapes URLs. This keeps the import flow pure JSON and avoids
outbound HTTP from the server.
"""

from __future__ import annotations

from typing import BinaryIO
from uuid import UUID

from app.application.bibliotheque.exceptions import (
    CompanyAccessDeniedError,
    ImageTooLargeError,
    InsufficientPermissionError,
    ProductNotFoundError,
    UnsupportedImageTypeError,
)
from app.application.bibliotheque.ports import (
    ICompanyMembershipReader,
    ICompanyPermissionChecker,
    ILibraryProductRepository,
    IProductImageStorage,
    TransactionalSessionPort,
)
from app.domain.entities.library_product import LibraryProduct
from app.domain.value_objects.image_signature import sniff_image_stream
from app.infrastructure.adapters.bibliotheque_image_storage import BibliothequeImageStorage

_MANAGE_PERMISSION = "bibliotheque:manage"

# Mirror the invoice attachment 10 MB cap.
IMAGE_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

# Allowlist of content-types accepted for product images.
_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


class UploadProductImageUseCase:
    """Store a product image and record its storage key on the product.

    Authorization: company member + bibliotheque:manage permission.
    """

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

    def authorize(self, *, requester_id: UUID, product_id: UUID) -> LibraryProduct:
        """Return the product once the requester may change its image.

        The route calls this before it reads the upload, so a caller who may not
        touch the product gets 404/403 rather than the upload's validation errors.

        Raises:
            ProductNotFoundError: product does not exist.
            CompanyAccessDeniedError: requester is not a company member.
            InsufficientPermissionError: requester lacks bibliotheque:manage.
        """
        product = self._product_repo.find_by_id(product_id)
        if product is None:
            raise ProductNotFoundError(f"Product {product_id} not found.")

        if not self._membership.is_member(requester_id, product.company_id):
            raise CompanyAccessDeniedError(f"User {requester_id} is not a member of company {product.company_id}.")
        if not self._permission_checker.has_permission_in_company(requester_id, _MANAGE_PERMISSION, product.company_id):
            raise InsufficientPermissionError(
                f"User {requester_id} lacks '{_MANAGE_PERMISSION}' in company " f"{product.company_id}."
            )
        return product

    def execute(
        self,
        *,
        requester_id: UUID,
        product_id: UUID,
        fileobj: BinaryIO,
        content_type: str,
        filename: str = "image",
        size_bytes: int = 0,
    ) -> str:
        """Upload image bytes, update the product's image_storage_key, return the key.

        Raises:
            ProductNotFoundError: product does not exist.
            CompanyAccessDeniedError: requester is not a company member.
            InsufficientPermissionError: requester lacks bibliotheque:manage.
            UnsupportedImageTypeError: content-type is not in the allowed set, or the
                bytes are not a PNG, JPEG or WebP image.
            ImageTooLargeError: uploaded bytes exceed IMAGE_MAX_SIZE_BYTES.
        """
        # Access first (404/403 before any 413/415), then the content-type and size,
        # both before storage is touched.
        product = self.authorize(requester_id=requester_id, product_id=product_id)

        if content_type not in _ALLOWED_IMAGE_TYPES:
            raise UnsupportedImageTypeError(
                f"Unsupported image type '{content_type}'. Allowed: {sorted(_ALLOWED_IMAGE_TYPES)}"
            )

        if size_bytes > IMAGE_MAX_SIZE_BYTES:
            raise ImageTooLargeError(
                f"Image size {size_bytes} bytes exceeds the {IMAGE_MAX_SIZE_BYTES // (1024 * 1024)} MB limit."
            )

        # The client's content-type is only a label: the bytes must really be an
        # allowed image, and they are stored under the type they actually are.
        detected_type = sniff_image_stream(fileobj)
        if detected_type not in _ALLOWED_IMAGE_TYPES:
            raise UnsupportedImageTypeError(
                f"File contents are not a PNG, JPEG or WebP image (sent as '{content_type}')."
            )

        # Always use a fixed object name derived from the sanitized basename — never
        # interpolate the raw client filename which can contain path-traversal sequences
        # like "../<other-product-id>/image".
        key = BibliothequeImageStorage.build_key(product_id)
        self._image_storage.put(key, fileobj, detected_type)

        updated = product.with_enrichment(image_storage_key=key)
        self._product_repo.upsert(updated)
        self._db.commit()
        return key
