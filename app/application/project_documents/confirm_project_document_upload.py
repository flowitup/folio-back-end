"""Use case: confirm a presigned upload — verify S3 object exists, persist DB row."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from app.application.project_documents.ports import (
    IDocumentStorage,
    IProjectDocumentRepository,
    ITransactionalSession,
)
from app.domain.project_document import ProjectDocument

_log = logging.getLogger(__name__)


class DocumentNotInStorageError(Exception):
    """Raised when confirm is called but the object is not found in S3."""

    pass


class ConfirmProjectDocumentUploadUseCase:
    """Verify the browser-uploaded object exists in S3, then persist metadata.

    This is the second half of the presigned upload flow. The browser has
    already PUT the file directly to S3/MinIO; this use case verifies it
    landed and records the DB row.
    """

    def __init__(
        self,
        repo: IProjectDocumentRepository,
        storage: IDocumentStorage,
        db_session: ITransactionalSession,
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._db_session = db_session

    def execute(
        self,
        *,
        project_id: UUID,
        doc_id: UUID,
        storage_key: str,
        filename: str,
        content_type: str,
        size_bytes: int,
        uploader_user_id: UUID,
    ) -> ProjectDocument:
        """Confirm the upload and return the persisted document.

        Raises:
            DocumentNotInStorageError: Object not found at storage_key.
        """
        # --- Verify the object actually landed in S3 ---
        head = self._storage.head_object(storage_key)
        if head is None:
            raise DocumentNotInStorageError(f"Object not found at key '{storage_key}' — upload may have failed")

        # Use actual size from S3 if available (more reliable than client-declared)
        actual_size = head.get("ContentLength", size_bytes)

        # --- Build entity with original filename preserved ---
        doc = ProjectDocument(
            id=doc_id,
            project_id=project_id,
            uploader_user_id=uploader_user_id,
            filename=filename,
            content_type=content_type,
            size_bytes=actual_size,
            storage_key=storage_key,
            created_at=datetime.now(timezone.utc),
            deleted_at=None,
        )

        try:
            saved = self._repo.save(doc)
            self._db_session.commit()
            return saved
        except Exception:
            # Orphan cleanup — same pattern as UploadProjectDocumentUseCase
            try:
                self._storage.delete(storage_key)
            except Exception:
                _log.warning(
                    "Failed to clean up orphaned storage object %s after DB commit failure",
                    storage_key,
                )
            raise
