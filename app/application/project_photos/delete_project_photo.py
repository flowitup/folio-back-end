"""Use case: soft-delete a project photo."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.application.project_photos.exceptions import PhotoNotFoundError, PhotoPermissionDeniedError
from app.application.project_photos.ports import IProjectPhotoRepository, ITransactionalSession


class DeleteProjectPhotoUseCase:
    """Soft-delete a project photo after verifying ownership and permissions.

    Storage objects (original + thumbnail) are NOT removed — soft-delete retains
    them for audit trails.  A background janitor is out of scope for v1.
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
        requester_user_id: UUID,
        project: object,
        is_admin: bool = False,
    ) -> None:
        """Soft-delete a photo if the requester has permission.

        Args:
            photo_id: UUID of the photo to delete.
            expected_project_id: Project UUID from the URL for cross-project guard.
            requester_user_id: UUID of the authenticated user requesting deletion.
            project: Project domain entity (must expose .id and .owner_id).
            is_admin: True if the requester holds a company-admin role.

        Raises:
            PhotoNotFoundError: Photo does not exist, is already soft-deleted,
                or belongs to a different project (existence is not leaked).
            PhotoPermissionDeniedError: Requester is neither the uploader,
                the project owner, nor an admin.
        """
        photo = self._repo.find_by_id(photo_id)

        # Treat missing and already-deleted identically to avoid enumeration.
        if photo is None or photo.deleted_at is not None:
            raise PhotoNotFoundError(f"Photo {photo_id} not found")

        # Cross-project guard: silently map to NotFound so callers cannot probe
        # photo existence across projects via DELETE on an unrelated project URL.
        if photo.project_id != expected_project_id:
            raise PhotoNotFoundError(f"Photo {photo_id} not found")

        allowed = (
            is_admin
            or photo.uploader_user_id == requester_user_id
            or project.owner_id == requester_user_id  # type: ignore[attr-defined]
        )
        if not allowed:
            raise PhotoPermissionDeniedError(f"User {requester_user_id} is not permitted to delete photo {photo_id}")

        self._repo.soft_delete(photo_id, datetime.now(timezone.utc))
        self._db_session.commit()
