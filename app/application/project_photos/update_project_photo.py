"""Use case: update caption and/or captured_at on a project photo."""

from __future__ import annotations

from uuid import UUID

from app.application.project_photos.exceptions import PhotoNotFoundError, PhotoPermissionDeniedError
from app.application.project_photos.ports import IProjectPhotoRepository, ITransactionalSession
from app.domain.project_photo import ProjectPhoto, _SENTINEL


class UpdateProjectPhotoUseCase:
    """Update mutable metadata fields on a photo after verifying ownership.

    Permission rule: admin OR uploader OR project owner may update.
    Both caption and captured_at are routed through the entity's with_updates()
    sentinel pattern so a caption-only PATCH never nulls out captured_at.
    """

    def __init__(
        self,
        repo: IProjectPhotoRepository,
        db_session: ITransactionalSession,
    ) -> None:
        self._repo = repo
        self._db_session = db_session

    def execute(
        self,
        photo_id: UUID,
        expected_project_id: UUID,
        *,
        caption: object = _SENTINEL,
        captured_at: object = _SENTINEL,
        requester_user_id: UUID,
        project: object,
        is_admin: bool = False,
    ) -> ProjectPhoto:
        """Update photo metadata and return the updated entity.

        Args:
            photo_id: UUID of the photo to update.
            expected_project_id: Project UUID from the URL for cross-project guard.
            caption: New caption, or _SENTINEL to leave unchanged.
            captured_at: New captured_at datetime, or _SENTINEL to leave unchanged.
            requester_user_id: UUID of the authenticated user.
            project: Project domain entity (must expose .id and .owner_id).
            is_admin: True if the requester holds a company-admin role.

        Returns:
            The updated ProjectPhoto entity.

        Raises:
            PhotoNotFoundError: Photo does not exist, is soft-deleted, or belongs
                to a different project.
            PhotoPermissionDeniedError: Requester lacks permission.
        """
        photo = self._repo.find_by_id(photo_id)
        if photo is None or photo.deleted_at is not None:
            raise PhotoNotFoundError(f"Photo {photo_id} not found")

        if photo.project_id != expected_project_id:
            raise PhotoNotFoundError(f"Photo {photo_id} not found")

        allowed = (
            is_admin
            or photo.uploader_user_id == requester_user_id
            or project.owner_id == requester_user_id  # type: ignore[attr-defined]
        )
        if not allowed:
            raise PhotoPermissionDeniedError(f"User {requester_user_id} is not permitted to update photo {photo_id}")

        # with_updates() respects sentinels — omitted fields are preserved unchanged.
        updated = photo.with_updates(caption=caption, captured_at=captured_at)

        self._repo.update_metadata(
            photo_id,
            caption=updated.caption,
            captured_at=updated.captured_at,
        )
        self._db_session.commit()
        return updated
