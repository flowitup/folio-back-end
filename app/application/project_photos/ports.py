"""Ports (interfaces) for the project photos application layer."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol, runtime_checkable
from uuid import UUID

from app.domain.project_photo import ProjectPhoto

# Reuse shared ports from project_documents — DRY, single source of truth.
from app.application.project_documents.ports import (  # noqa: F401
    IDocumentStorage,
    ITransactionalSession,
    IFilenameSanitizer,
)


@runtime_checkable
class IProjectPhotoRepository(Protocol):
    """Port defining the project photo persistence contract."""

    def save(self, photo: ProjectPhoto) -> ProjectPhoto:
        """Persist a new photo record and return the saved entity."""
        ...

    def find_by_id(self, photo_id: UUID) -> Optional[ProjectPhoto]:
        """Return the photo or None if not found (includes soft-deleted; callers check deleted_at)."""
        ...

    def list_for_project(self, project_id: UUID, page: int, per_page: int) -> tuple[list[ProjectPhoto], int]:
        """Return (items, total) for active photos, ordered captured_at DESC, created_at DESC."""
        ...

    def update_metadata(self, photo_id: UUID, caption: Optional[str], captured_at: datetime) -> None:
        """UPDATE caption and captured_at for the given photo."""
        ...

    def soft_delete(self, photo_id: UUID, now: datetime) -> None:
        """Mark the photo as deleted by setting deleted_at = now."""
        ...


@runtime_checkable
class IImageThumbnailer(Protocol):
    """Port for generating a server-side JPEG thumbnail from image bytes.

    Implementations must:
    - Apply EXIF orientation correction before resizing.
    - Constrain both edges to 480 px (aspect-preserving).
    - Return JPEG bytes regardless of input format.
    """

    def generate(self, data: bytes, content_type: str) -> bytes:
        """Return JPEG thumbnail bytes for the given image data.

        Args:
            data: Raw image bytes (JPEG, PNG, or WebP).
            content_type: MIME type of the input (informational; implementations
                          should still rely on file header sniffing for safety).

        Returns:
            JPEG-encoded thumbnail bytes.

        Raises:
            ThumbnailGenerationError: If the image cannot be decoded or processed.
        """
        ...
