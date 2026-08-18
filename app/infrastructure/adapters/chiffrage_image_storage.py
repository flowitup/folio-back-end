"""ChiffrageImageStorage — S3/MinIO image storage for chiffrage articles.

Reuses the bibliothèque adapter wholesale: put/get_stream already take an
explicit key, so only the key namespace differs. Article images live under
chiffrage-articles/{article_id}/ so they are separable from library-product
images and invoice attachments sharing the same bucket.
"""

from __future__ import annotations

from uuid import UUID

from app.infrastructure.adapters.bibliotheque_image_storage import BibliothequeImageStorage

_KEY_PREFIX = "chiffrage-articles"
# Fixed object name — a client-supplied filename is never interpolated into the
# key, which is what keeps multipart upload free of path traversal.
_IMAGE_OBJECT_NAME = "image"


class ChiffrageImageStorage(BibliothequeImageStorage):
    """Same object store, article-scoped keys."""

    @staticmethod
    def build_key(article_id: UUID) -> str:  # type: ignore[override]
        """Build the canonical S3 key for an article's image."""
        return f"{_KEY_PREFIX}/{article_id}/{_IMAGE_OBJECT_NAME}"
