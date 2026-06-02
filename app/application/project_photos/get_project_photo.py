"""Use case: retrieve a project photo and open its storage stream."""

from __future__ import annotations

from typing import BinaryIO, Literal
from uuid import UUID

from app.application.project_photos.exceptions import PhotoNotFoundError
from app.application.project_photos.ports import IDocumentStorage, IProjectPhotoRepository
from app.domain.project_photo import ProjectPhoto


class GetProjectPhotoUseCase:
    """Look up a photo and open a download stream from object storage.

    The cross-project ownership invariant is enforced BEFORE opening the
    storage stream so that a member of project B who guesses a project-A
    photo UUID cannot exhaust backend→storage TCP connections via repeated
    probing (mirrors the documents pattern).
    """

    def __init__(
        self,
        repo: IProjectPhotoRepository,
        storage: IDocumentStorage,
    ) -> None:
        self._repo = repo
        self._storage = storage

    def execute(
        self,
        photo_id: UUID,
        expected_project_id: UUID,
        *,
        variant: Literal["original", "thumbnail"],
    ) -> tuple[ProjectPhoto, BinaryIO, int]:
        """Return the photo entity, a readable binary stream, and its byte length.

        Args:
            photo_id: UUID of the photo to retrieve.
            expected_project_id: Project UUID from the URL; must match the photo's
                project_id. Returns PhotoNotFoundError on mismatch so that
                existence of foreign-project photos is not leaked.
            variant: "original" streams the full-resolution image; "thumbnail"
                streams the JPEG thumbnail.

        Returns:
            A 3-tuple of (ProjectPhoto, file-like stream, content_length_bytes).

        Raises:
            PhotoNotFoundError: Photo does not exist, has been soft-deleted,
                or does not belong to expected_project_id.
        """
        photo = self._repo.find_by_id(photo_id)
        if photo is None or photo.deleted_at is not None:
            raise PhotoNotFoundError(f"Photo {photo_id} not found")

        # Cross-project guard: enforce before opening the storage stream.
        if photo.project_id != expected_project_id:
            raise PhotoNotFoundError(f"Photo {photo_id} not found")

        storage_key = photo.storage_key if variant == "original" else photo.thumbnail_storage_key
        stream, length = self._storage.get_stream(storage_key)
        return photo, stream, length
