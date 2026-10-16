"""Use case: confirm a presigned upload — verify S3 object exists, persist DB row."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from app.application.project_documents.exceptions import (
    FilenameTooLongError,
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
from app.application.project_documents.upload_project_document import (
    MAX_SIZE_BYTES,
    stored_content_type,
    validate_file_type,
)
from app.domain.project_document import ProjectDocument
from app.domain.value_objects.display_filename import MAX_DISPLAY_FILENAME_LENGTH, is_too_long, strip_control_chars

_log = logging.getLogger(__name__)

_NO_ROW = object()


class DocumentNotInStorageError(Exception):
    """Raised when confirm is called but the object is not found in S3."""

    pass


class StorageKeyMismatchError(Exception):
    """Raised when the storage key is not the one presign issued for this doc_id and filename."""

    pass


class DocumentAlreadyConfirmedError(Exception):
    """Raised when doc_id already has a row that this confirm cannot be a retry of (e.g. it was deleted)."""

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
            DocumentAlreadyConfirmedError: doc_id already has a deleted or foreign row.
                A repeated confirm of a live document just returns it; neither case
                ever deletes the stored object, which that row owns.
            DocumentNotInStorageError: Object not found at storage_key.
            EmptyFileError / DocumentFileTooLargeError: The stored object is
                empty or over MAX_SIZE_BYTES (it is deleted).
        """
        if is_too_long(filename):
            raise FilenameTooLongError(f"File name is too long (max {MAX_DISPLAY_FILENAME_LENGTH} characters)")
        sanitized = self._sanitizer.sanitize(filename)
        if not sanitized:
            raise UnsupportedDocumentTypeError("Invalid filename after sanitation — no safe characters remain")
        validate_file_type(sanitized, content_type)
        if storage_key != f"project-documents/{project_id}/{doc_id}/{sanitized}":
            raise StorageKeyMismatchError("storage_key does not match this document and filename")

        # --- A retry (double click, timeout) of a confirm that already succeeded ---
        existing = self._repo.find_by_id(doc_id)
        if existing is not None:
            return self._existing_or_conflict(existing, project_id, storage_key)

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

        # --- Build entity with original filename preserved (minus control characters) ---
        doc = ProjectDocument(
            id=doc_id,
            project_id=project_id,
            uploader_user_id=uploader_user_id,
            filename=strip_control_chars(filename),
            content_type=stored_content_type(content_type),
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
            # Orphan cleanup, but only when no row owns the object: a concurrent confirm of
            # the same document may have just saved it, and its file must survive.
            winner = self._row_owning(doc_id)
            if winner is _NO_ROW:
                self._discard(storage_key)
            elif isinstance(winner, ProjectDocument):
                try:
                    return self._existing_or_conflict(winner, project_id, storage_key)
                except DocumentAlreadyConfirmedError:
                    pass
            raise

    @staticmethod
    def _existing_or_conflict(existing: ProjectDocument, project_id: UUID, storage_key: str) -> ProjectDocument:
        if existing.deleted_at is None and existing.project_id == project_id and existing.storage_key == storage_key:
            return existing
        raise DocumentAlreadyConfirmedError("A document with this id already exists")

    def _row_owning(self, doc_id: UUID) -> object:
        """The row for doc_id after a failed save, _NO_ROW when none exists, None when that cannot be told."""
        try:
            self._db_session.rollback()
            row = self._repo.find_by_id(doc_id)
        except Exception:
            _log.warning("Could not check whether document %s was saved; keeping its storage object", doc_id)
            return None
        return _NO_ROW if row is None else row

    def _discard(self, storage_key: str) -> None:
        """Delete an object that will never get a DB row; a failure is only logged."""
        try:
            self._storage.delete(storage_key)
        except Exception:
            _log.warning("Failed to clean up orphaned storage object %s", storage_key)
