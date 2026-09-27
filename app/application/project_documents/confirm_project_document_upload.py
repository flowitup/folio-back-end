"""Use case: confirm a presigned upload — verify S3 object exists, persist DB row."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from app.application.project_documents.exceptions import (
    DocumentFileTooLargeError,
    EmptyFileError,
    UnsupportedDocumentTypeError,
)
from app.application.project_documents.ports import (
    IDocumentStorage,
    IFilenameSanitizer,
    IProjectDocumentRepository,
    ITransactionalSession,
)
from app.application.project_documents.upload_project_document import MAX_SIZE_BYTES, validate_file_type
from app.domain.project_document import ProjectDocument

_log = logging.getLogger(__name__)


class DocumentNotInStorageError(Exception):
    """Raised when confirm is called but the object is not found in S3."""

    pass


class StorageKeyMismatchError(Exception):
    """Raised when the storage key is not the one presign issued for this doc_id and filename."""

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
        filename_sanitizer: IFilenameSanitizer,
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._db_session = db_session
        self._sanitizer = filename_sanitizer

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

        The presigned PUT binds neither the size nor the type, so the rules of the
        multipart path are applied again here, to what actually landed in storage.

        Raises:
            UnsupportedDocumentTypeError: Filename or MIME type not allowed.
            StorageKeyMismatchError: storage_key is not the key presign issued
                for this doc_id and filename.
            DocumentNotInStorageError: Object not found at storage_key.
            EmptyFileError / DocumentFileTooLargeError: The stored object is
                empty or over MAX_SIZE_BYTES (it is deleted).
        """
        sanitized = self._sanitizer.sanitize(filename)
        if not sanitized:
            raise UnsupportedDocumentTypeError("Invalid filename after sanitation — no safe characters remain")
        validate_file_type(sanitized, content_type)
        if storage_key != f"project-documents/{project_id}/{doc_id}/{sanitized}":
            raise StorageKeyMismatchError("storage_key does not match this document and filename")

        # --- Verify the object actually landed in S3 ---
        head = self._storage.head_object(storage_key)
        if head is None:
            raise DocumentNotInStorageError(f"Object not found at key '{storage_key}' — upload may have failed")

        # The stored size is the real one; the declared size is only a fallback.
        actual_size = head.get("ContentLength", size_bytes)
        if actual_size <= 0 or actual_size > MAX_SIZE_BYTES:
            self._discard(storage_key)
            if actual_size <= 0:
                raise EmptyFileError("Uploaded file has no content (size <= 0 bytes)")
            raise DocumentFileTooLargeError(f"File size {actual_size} bytes exceeds maximum of {MAX_SIZE_BYTES} bytes")

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
            self._discard(storage_key)
            raise

    def _discard(self, storage_key: str) -> None:
        """Delete an object that will never get a DB row; a failure is only logged."""
        try:
            self._storage.delete(storage_key)
        except Exception:
            _log.warning("Failed to clean up orphaned storage object %s", storage_key)
