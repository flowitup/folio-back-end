"""FetchProductImageFromUrlUseCase — server-side image fetch from an allowlisted URL.

Product photos from Leroy Merlin's CDN (media.adeo.com) are hotlink-protected and
cannot be fetched from the browser.  This use-case fetches the bytes server-side
using a browser-like Referer / User-Agent, validates them, then stores them via the
standard BibliothequeImageStorage path.

SSRF protection:
  - Only HTTPS URLs are accepted.
  - The request host must match the allowlist (_ALLOWED_HOSTS).
  - Redirects are disabled; if the server issues a redirect we reject it rather than
    follow it to an arbitrary host.

Rate-limit note: the route applies a generous per-user rate limit (see routes.py).
"""

from __future__ import annotations

import fnmatch
import logging
from io import BytesIO
from uuid import UUID

import httpx

from app.application.bibliotheque.exceptions import (
    CompanyAccessDeniedError,
    ImageTooLargeError,
    InsufficientPermissionError,
    ProductNotFoundError,
    SsrfBlockedError,
    UnsupportedImageTypeError,
)
from app.application.bibliotheque.ports import (
    ICompanyMembershipReader,
    ICompanyPermissionChecker,
    ILibraryProductRepository,
    IProductImageStorage,
    TransactionalSessionPort,
)
from app.infrastructure.adapters.bibliotheque_image_storage import BibliothequeImageStorage

_log = logging.getLogger(__name__)

_MANAGE_PERMISSION = "bibliotheque:manage"

# Maximum image size that will be fetched / stored (10 MB — same cap as multipart upload).
IMAGE_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

# Content-types accepted from the remote server.
_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}

# SSRF allowlist — glob patterns matched against the request host (case-insensitive).
# Add entries here to extend support to new CDNs without code changes elsewhere.
SSRF_ALLOWED_HOST_PATTERNS: tuple[str, ...] = (
    "media.adeo.com",
    "*.adeo.com",
    "*.leroymerlin.fr",
)

# Headers sent to the upstream CDN to bypass hotlink protection.
_FETCH_HEADERS = {
    "Referer": "https://www.leroymerlin.fr/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}

_FETCH_TIMEOUT_SECONDS = 10


def _is_host_allowed(host: str) -> bool:
    """Return True if host matches any pattern in the SSRF allowlist."""
    host_lower = host.lower()
    for pattern in SSRF_ALLOWED_HOST_PATTERNS:
        if fnmatch.fnmatch(host_lower, pattern.lower()):
            return True
    return False


class FetchProductImageFromUrlUseCase:
    """Fetch an image from an allowlisted URL and store it as a product image.

    Authorization: company member + bibliotheque:manage permission (same as upload).
    Idempotent by default: if the product already has an image_storage_key the call
    returns successfully without re-fetching.  Pass force=True to overwrite.
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

    def execute(
        self,
        *,
        requester_id: UUID,
        product_id: UUID,
        url: str,
        force: bool = False,
    ) -> str:
        """Fetch image from URL and store it. Returns the storage key.

        Raises:
            SsrfBlockedError: URL is not HTTPS or host not in allowlist.
            ProductNotFoundError: product does not exist.
            CompanyAccessDeniedError: requester is not a company member.
            InsufficientPermissionError: requester lacks bibliotheque:manage.
            ImageAlreadyExistsError: product already has an image and force=False.
            UnsupportedImageTypeError: remote content-type not in allowed set.
            ImageTooLargeError: remote bytes exceed IMAGE_MAX_SIZE_BYTES.
        """
        # --- SSRF validation (before any DB access) ---
        self._validate_url(url)

        # --- Product + auth checks ---
        product = self._product_repo.find_by_id(product_id)
        if product is None:
            raise ProductNotFoundError(f"Product {product_id} not found.")

        if not self._membership.is_member(requester_id, product.company_id):
            raise CompanyAccessDeniedError(f"User {requester_id} is not a member of company {product.company_id}.")
        if not self._permission_checker.has_permission(requester_id, _MANAGE_PERMISSION):
            raise InsufficientPermissionError(f"User {requester_id} lacks '{_MANAGE_PERMISSION}' permission.")

        # --- Idempotency: skip if already has image (unless force=True) ---
        if product.image_storage_key is not None and not force:
            return product.image_storage_key

        # --- Fetch image bytes from upstream CDN ---
        image_bytes, content_type = self._fetch_image(url)

        # --- Store + persist ---
        key = BibliothequeImageStorage.build_key(product_id)
        self._image_storage.put(key, BytesIO(image_bytes), content_type)

        updated = product.with_enrichment(image_storage_key=key) if not force else _force_image_key(product, key)
        self._product_repo.upsert(updated)
        self._db.commit()
        return key

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_url(url: str) -> None:
        """Raise SsrfBlockedError if URL is not HTTPS or host not in allowlist."""
        try:
            parsed = httpx.URL(url)
        except Exception as exc:
            raise SsrfBlockedError(f"Malformed URL: {url!r}") from exc

        if parsed.scheme != "https":
            raise SsrfBlockedError(f"Only HTTPS URLs are accepted, got scheme '{parsed.scheme}'.")

        host = parsed.host
        if not _is_host_allowed(host):
            raise SsrfBlockedError(
                f"Host '{host}' is not in the image fetch allowlist. " f"Allowed patterns: {SSRF_ALLOWED_HOST_PATTERNS}"
            )

    @staticmethod
    def _fetch_image(url: str) -> tuple[bytes, str]:
        """Fetch image bytes. Raises UnsupportedImageTypeError or ImageTooLargeError on bad response."""
        try:
            with httpx.Client(follow_redirects=False, timeout=_FETCH_TIMEOUT_SECONDS) as client:
                response = client.get(url, headers=_FETCH_HEADERS)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Remote server returned HTTP {exc.response.status_code} for {url!r}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"HTTP request to {url!r} failed: {exc}") from exc

        # Validate content-type from response headers.
        raw_ct = response.headers.get("content-type", "")
        # Strip parameters like "; charset=utf-8"
        content_type = raw_ct.split(";")[0].strip().lower()
        if content_type not in _ALLOWED_IMAGE_TYPES:
            raise UnsupportedImageTypeError(
                f"Remote content-type '{content_type}' is not an accepted image type. "
                f"Allowed: {sorted(_ALLOWED_IMAGE_TYPES)}"
            )

        image_bytes = response.content
        if len(image_bytes) > IMAGE_MAX_SIZE_BYTES:
            raise ImageTooLargeError(
                f"Remote image size {len(image_bytes)} bytes exceeds the "
                f"{IMAGE_MAX_SIZE_BYTES // (1024 * 1024)} MB limit."
            )

        return image_bytes, content_type


def _force_image_key(product, key: str):  # type: ignore[no-untyped-def]
    """Return product copy with image_storage_key set unconditionally (force-overwrite)."""
    from dataclasses import replace
    from datetime import datetime, timezone

    return replace(product, image_storage_key=key, updated_at=datetime.now(timezone.utc))
