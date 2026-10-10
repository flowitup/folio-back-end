"""Delete project use case."""

import logging
from typing import Optional
from uuid import UUID

from app.application.projects.ports import IProjectRepository, ObjectDeleterPort, ProjectStorageKeyReaderPort
from app.domain.exceptions.project_exceptions import ProjectNotFoundError

logger = logging.getLogger(__name__)


class DeleteProjectUseCase:
    """Delete a project and the stored files of its documents, photos, attachments, etc.

    FK cascades drop the child rows but cannot reach the object store, and the purge
    janitor for soft-deleted files works from those rows, so the keys are snapshotted
    first. The row delete wins: storage is cleaned best-effort afterwards and a failure
    is only logged, as in DeleteInvoiceUseCase.
    """

    def __init__(
        self,
        project_repo: IProjectRepository,
        storage_keys: Optional[ProjectStorageKeyReaderPort] = None,
        storage: Optional[ObjectDeleterPort] = None,
    ):
        self._repo = project_repo
        self._storage_keys = storage_keys
        self._storage = storage

    def execute(self, project_id: UUID) -> None:
        project = self._repo.find_by_id(project_id)
        if not project:
            raise ProjectNotFoundError(str(project_id))

        storage = self._storage
        keys: list[str] = []
        if self._storage_keys and storage:
            keys = self._storage_keys.storage_keys_for_project(project_id)

        self._repo.delete(project_id)

        if storage is None:
            return
        for key in keys:
            try:
                storage.delete(key)
            except Exception as exc:
                logger.warning("Failed to delete S3 object %s for project %s: %s", key, project_id, exc)
