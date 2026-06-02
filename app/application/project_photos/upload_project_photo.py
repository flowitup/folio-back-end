"""Use case: upload a progress photo to a project."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from io import BytesIO
from uuid import UUID, uuid4

from app.application.project_photos.exceptions import (
    EmptyImageError,
    ImageTooLargeError,
    ThumbnailGenerationError,
    UnsupportedImageTypeError,
)
from app.application.project_photos.ports import (
    IDocumentStorage,
    IFilenameSanitizer,
    IImageThumbnailer,
    IProjectPhotoRepository,
    ITransactionalSession,
)
from app.domain.project_photo import ProjectPhoto

_log = logging.getLogger(__name__)

# Default 25 MiB — images don't need the 150 MiB document headroom.
MAX_SIZE_BYTES = int(os.environ.get("PROJECT_PHOTO_MAX_SIZE_BYTES", str(25 * 1024 * 1024)))

ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".webp"})

ALLOWED_MIME_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/png", "image/webp"})


def validate_image_type(filename: str, mime_type: str) -> None:
    """Validate that both the extension and MIME type are in the image allowlist.

    Args:
        filename: Sanitized filename (already through the filename sanitizer).
        mime_type: MIME type reported by the client.

    Raises:
        UnsupportedImageTypeError: Extension or MIME type not in the allowlist.
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UnsupportedImageTypeError(f"Extension '{ext}' is not allowed — must be one of {ALLOWED_EXTENSIONS}")
    # Some browsers/proxies send generic octet-stream; allow it as a fallback.
    if mime_type not in ALLOWED_MIME_TYPES and mime_type != "application/octet-stream":
        raise UnsupportedImageTypeError(f"MIME type '{mime_type}' is not allowed for image uploads")


class UploadProjectPhotoUseCase:
    """Validate, thumbnail, store, and persist metadata for a project progress photo."""

    def __init__(
        self,
        repo: IProjectPhotoRepository,
        storage: IDocumentStorage,
        thumbnailer: IImageThumbnailer,
        db_session: ITransactionalSession,
        filename_sanitizer: IFilenameSanitizer,
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._thumbnailer = thumbnailer
        self._db_session = db_session
        self._filename_sanitizer = filename_sanitizer

    def execute(
        self,
        *,
        project_id: UUID,
        filename: str,
        content_type: str,
        size_bytes: int,
        data: bytes,
        uploader_user_id: UUID,
        caption: str | None,
        captured_at: datetime | None,
    ) -> ProjectPhoto:
        """Upload a photo and return the persisted domain entity.

        Steps:
        1. Size guard (empty → 400, oversize → 413).
        2. Filename sanitization + type allowlist check.
        3. Generate thumbnail via thumbnailer.
        4. PUT original + thumbnail to object storage.
        5. Persist metadata row and commit.
        6. On commit failure, delete both storage objects (orphan cleanup).

        Args:
            project_id: UUID of the target project.
            filename: Original filename as provided by the client.
            content_type: MIME type reported by the client.
            size_bytes: Byte count of the uploaded data.
            data: Raw image bytes.
            uploader_user_id: Authenticated user performing the upload.
            caption: Optional progress description.
            captured_at: Optional date/time the photo was taken; defaults to now.

        Returns:
            The saved ProjectPhoto entity.
        """
        # --- Size validation ---
        if size_bytes <= 0:
            raise EmptyImageError("Uploaded image has no content (size <= 0 bytes)")
        if size_bytes > MAX_SIZE_BYTES:
            raise ImageTooLargeError(f"Image size {size_bytes} bytes exceeds maximum of {MAX_SIZE_BYTES} bytes")

        # --- Filename sanitization ---
        sanitized = self._filename_sanitizer.sanitize(filename)
        if not sanitized:
            raise UnsupportedImageTypeError("Invalid filename after sanitization — no safe characters remain")

        # --- Type allowlist (defense-in-depth: sanitized name + MIME check) ---
        validate_image_type(sanitized, content_type)

        # --- Build storage keys using sanitized filename to prevent path traversal ---
        photo_id = uuid4()
        original_key = f"project-photos/{project_id}/{photo_id}/original/{sanitized}"
        thumb_key = f"project-photos/{project_id}/{photo_id}/thumb.jpg"

        # --- Generate thumbnail before uploading so a bad image fails fast ---
        try:
            thumb_bytes = self._thumbnailer.generate(data, content_type)
        except ThumbnailGenerationError:
            raise
        except Exception as exc:
            raise ThumbnailGenerationError(f"Thumbnail generation failed: {exc}") from exc

        # --- Upload original then thumbnail ---
        self._storage.put(original_key, BytesIO(data), content_type)
        self._storage.put(thumb_key, BytesIO(thumb_bytes), "image/jpeg")

        # --- Default captured_at to now if not provided ---
        effective_captured_at = captured_at or datetime.now(timezone.utc)

        photo = ProjectPhoto(
            id=photo_id,
            project_id=project_id,
            uploader_user_id=uploader_user_id,
            filename=filename,  # original filename preserved in DB
            content_type=content_type,
            size_bytes=size_bytes,
            storage_key=original_key,
            thumbnail_storage_key=thumb_key,
            caption=caption,
            captured_at=effective_captured_at,
            created_at=datetime.now(timezone.utc),
            deleted_at=None,
        )

        try:
            saved = self._repo.save(photo)
            self._db_session.commit()
            return saved
        except Exception:
            # Orphan cleanup: remove both storage objects so object store stays
            # consistent with the DB on commit failure.
            for key in (original_key, thumb_key):
                try:
                    self._storage.delete(key)
                except Exception:
                    _log.warning(
                        "Failed to clean up orphaned storage object %s after DB commit failure",
                        key,
                    )
            raise
