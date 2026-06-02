"""ProjectPhoto domain entity — metadata for a progress photo attached to a project."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional
from uuid import UUID

# Sentinel object used by with_updates() to distinguish "field not supplied"
# from "field explicitly set to None".  A caller that passes caption=None means
# "clear the caption"; a caller that omits caption means "leave it unchanged".
_SENTINEL = object()


@dataclass(frozen=True)
class ProjectPhoto:
    """Domain entity representing a construction-progress photo attached to a project.

    The image bytes live in object storage (MinIO/S3).  This entity holds only
    the metadata + storage keys needed to retrieve and manage them.

    Two storage keys are tracked: the full-resolution original and a server-side
    generated JPEG thumbnail (max 480 px on either edge, EXIF-corrected) used
    for low-bandwidth thumbnails. Keeping both keys in the entity makes the
    get-stream use-case variant-aware without touching storage.

    Soft-deletion is tracked via deleted_at.
    """

    id: UUID
    project_id: UUID
    uploader_user_id: UUID
    filename: str  # original filename as uploaded
    content_type: str  # MIME type of the original image
    size_bytes: int
    storage_key: str  # full-resolution object key in object storage
    thumbnail_storage_key: str  # server-generated JPEG thumbnail key
    caption: Optional[str]  # optional progress description (free text)
    captured_at: datetime  # date the photo was taken (tz-aware UTC)
    created_at: datetime
    deleted_at: Optional[datetime] = None

    def with_updates(
        self,
        *,
        caption: object = _SENTINEL,
        captured_at: object = _SENTINEL,
    ) -> "ProjectPhoto":
        """Return a new entity with only the supplied fields updated.

        Fields omitted by the caller (still pointing at the sentinel) are
        copied from the current entity unchanged.  This prevents a caption-only
        PATCH from silently nulling out captured_at and vice-versa.
        """
        return replace(
            self,
            caption=self.caption if caption is _SENTINEL else caption,  # type: ignore[arg-type]
            captured_at=self.captured_at if captured_at is _SENTINEL else captured_at,  # type: ignore[arg-type]
        )
