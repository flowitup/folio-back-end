"""Article photo use-cases: upload, fetch-from-URL, delete, read.

Bytes are stored in the object store and always streamed back THROUGH the API.
Two reasons this is not a plain external image URL on the article:

  * the production CSP is ``img-src 'self' data: blob: …`` — a supplier CDN URL
    rendered directly would be blocked in the browser and silently show nothing;
  * supplier CDNs are hotlink-protected and reorganise their paths, so a stored
    link rots while a stored image does not.
"""

from __future__ import annotations

import fnmatch
import logging
from io import BytesIO
from typing import BinaryIO
from urllib.parse import urlparse
from uuid import UUID

import httpx

from app.application.chiffrage.exceptions import (
    ArticleImageNotFoundError,
    ImageTooLargeError,
    SsrfBlockedError,
    UnsupportedImageTypeError,
)
from app.application.chiffrage.ports import (
    ChiffrageRepositoryPort,
    IArticleImageStorage,
    TransactionalSessionPort,
)
from app.application.chiffrage.validation import owned_article

_log = logging.getLogger(__name__)

IMAGE_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB — same cap as library product images.
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}

# SSRF allowlist — glob patterns matched case-insensitively against the host.
# Widened beyond the bibliothèque list because a chiffrage covers whichever
# fournisseurs the chantier actually buys from. The guard itself is unchanged:
# HTTPS only, host must match, redirects refused rather than followed.
SSRF_ALLOWED_HOST_PATTERNS: tuple[str, ...] = (
    "media.adeo.com",
    "*.adeo.com",
    "*.leroymerlin.fr",
    "*.pointp.fr",
    "*.saint-gobain.com",
    "*.rexel.fr",
    "*.castorama.fr",
    "*.bricodepot.fr",
    "*.cedeo.fr",
    "*.brossette.fr",
)

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


def is_host_allowed(host: str) -> bool:
    """Return True if *host* matches the SSRF allowlist."""
    h = (host or "").lower()
    return any(fnmatch.fnmatch(h, pattern) for pattern in SSRF_ALLOWED_HOST_PATTERNS)


def _validate_type(content_type: str) -> str:
    """Normalise and validate an image content-type."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct not in ALLOWED_IMAGE_TYPES:
        raise UnsupportedImageTypeError(f"Unsupported image type: {ct or 'unknown'}.")
    return ct


class UploadArticleImageUseCase:
    """Store an uploaded photo for an article."""

    def __init__(
        self,
        repo: ChiffrageRepositoryPort,
        storage: IArticleImageStorage,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._db = db_session

    def execute(self, *, project_id: UUID, article_id: UUID, fileobj: BinaryIO, content_type: str, size: int) -> None:
        article = owned_article(self._repo, article_id, project_id)
        if size > IMAGE_MAX_SIZE_BYTES:
            raise ImageTooLargeError(f"Image exceeds {IMAGE_MAX_SIZE_BYTES // (1024 * 1024)} MB.")
        ct = _validate_type(content_type)

        key = self._storage.build_key(article_id)
        self._storage.put(key, fileobj, ct)
        self._repo.save_article(article.with_image_key(key))
        self._db.commit()


class SetArticleImageFromUrlUseCase:
    """Fetch a supplier image server-side and store it for an article."""

    def __init__(
        self,
        repo: ChiffrageRepositoryPort,
        storage: IArticleImageStorage,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._db = db_session

    def execute(self, *, project_id: UUID, article_id: UUID, url: str) -> None:
        article = owned_article(self._repo, article_id, project_id)

        parsed = urlparse(url or "")
        if parsed.scheme != "https":
            raise SsrfBlockedError("Only https image URLs are accepted.")
        if not is_host_allowed(parsed.hostname or ""):
            raise SsrfBlockedError(f"Host not allowed: {parsed.hostname or 'unknown'}.")

        try:
            # follow_redirects stays off: a redirect could land on an arbitrary
            # host and slip past the allowlist we just checked.
            resp = httpx.get(
                url,
                headers=_FETCH_HEADERS,
                timeout=_FETCH_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise SsrfBlockedError(f"Could not fetch the image: {exc}") from exc

        if resp.status_code >= 300:
            raise SsrfBlockedError(f"Upstream returned {resp.status_code}.")

        content = resp.content
        if len(content) > IMAGE_MAX_SIZE_BYTES:
            raise ImageTooLargeError(f"Image exceeds {IMAGE_MAX_SIZE_BYTES // (1024 * 1024)} MB.")
        ct = _validate_type(resp.headers.get("content-type", ""))

        key = self._storage.build_key(article_id)
        self._storage.put(key, BytesIO(content), ct)
        self._repo.save_article(article.with_image_key(key))
        self._db.commit()


class DeleteArticleImageUseCase:
    """Detach an article's photo.

    The stored object is left in place: the key is deterministic, so the next
    upload overwrites it, and an orphaned blob is cheaper than a failed delete
    rolling back a committed row.
    """

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, project_id: UUID, article_id: UUID) -> None:
        article = owned_article(self._repo, article_id, project_id)
        if not article.image_storage_key:
            raise ArticleImageNotFoundError(f"Article {article_id} has no image.")
        self._repo.save_article(article.with_image_key(None))
        self._db.commit()


class GetArticleImageUseCase:
    """Stream an article's photo back to the client."""

    def __init__(self, repo: ChiffrageRepositoryPort, storage: IArticleImageStorage) -> None:
        self._repo = repo
        self._storage = storage

    def execute(self, *, project_id: UUID, article_id: UUID) -> tuple[BinaryIO, int, str]:
        article = owned_article(self._repo, article_id, project_id)
        if not article.image_storage_key:
            raise ArticleImageNotFoundError(f"Article {article_id} has no image.")
        return self._storage.get_stream(article.image_storage_key)
