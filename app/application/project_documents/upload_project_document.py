"""Use case: upload a file to a project's document library."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import BinaryIO
from uuid import UUID, uuid4

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
from app.domain.project_document import ProjectDocument, kind_for_extension

_log = logging.getLogger(__name__)

# Default 25 MB; override via environment variable.
MAX_SIZE_BYTES = int(os.environ.get("PROJECT_DOCUMENT_MAX_SIZE_BYTES", "26214400"))

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".docx", ".xlsx", ".dwg", ".txt"}
)

ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        # DWG MIME types vary widely across clients — accepted by extension only.
        "application/acad",
        "application/x-acad",
        "application/dwg",
        "image/vnd.dwg",
        "image/x-dwg",
    }
)


def validate_file_type(filename: str, mime_type: str) -> str:
    """Validate extension and MIME type; return the kind tag on success.

    Args:
        filename: Sanitized filename (already through secure_filename).
        mime_type: MIME type reported by the client.

    Returns:
        Kind tag: one of pdf | image | spreadsheet | doc | cad | text | other.

    Raises:
        UnsupportedDocumentTypeError: If the extension is not in the allowlist,
            or the MIME type does not match for non-DWG files.
    """
    ext = os.path.splitext(filename)[1].lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise UnsupportedDocumentTypeError(f"Extension '{ext}' is not allowed")

    # DWG MIME types are unstable across clients — accept by extension alone.
    if ext == ".dwg":
        return "cad"

    # For all other extensions, MIME must be in the allowlist or be the generic
    # octet-stream (some browsers/proxies send this for unknown types).
    if mime_type not in ALLOWED_MIME_TYPES and mime_type != "application/octet-stream":
        raise UnsupportedDocumentTypeError(f"MIME type '{mime_type}' is not allowed for extension '{ext}'")

    # Derive kind tag from extension (single source of truth: domain.EXT_TO_KIND).
    return kind_for_extension(ext)


class UploadProjectDocumentUseCase:
    """Validates, stores the file in object storage, and persists the metadata row."""

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
        self._filename_sanitizer = filename_sanitizer

    def execute(
        self,
        *,
        project_id: UUID,
        filename: str,
        content_type: str,
        size_bytes: int,
        fileobj: BinaryIO,
        uploader_user_id: UUID,
    ) -> ProjectDocument:
        """Upload a document and return the persisted domain entity.

        Args:
            project_id: UUID of the target project.
            filename: Original filename as provided by the client (kept in DB).
            content_type: MIME type reported by the client.
            size_bytes: Declared byte size (validated against MAX_SIZE_BYTES).
            fileobj: Readable binary stream of the file content.
            uploader_user_id: UUID of the authenticated user performing the upload.

        Returns:
            The saved ProjectDocument entity.

        Raises:
            DocumentFileTooLargeError: File is empty or exceeds MAX_SIZE_BYTES.
            UnsupportedDocumentTypeError: Extension or MIME type not in allowlist,
                or filename reduces to empty after sanitation.
        """
        # --- Size validation ---
        if size_bytes <= 0:
            # Empty file is a bad request, not an oversize error (M3).
            raise EmptyFileError("Uploaded file has no content (size <= 0 bytes)")
        if size_bytes > MAX_SIZE_BYTES:
            raise DocumentFileTooLargeError(f"File size {size_bytes} bytes exceeds maximum of {MAX_SIZE_BYTES} bytes")

        # --- Filename sanitation (strips path separators, null bytes, etc.) ---
        sanitized = self._filename_sanitizer.sanitize(filename)
        if not sanitized:
            raise UnsupportedDocumentTypeError("Invalid filename after sanitation — no safe characters remain")

        # --- Type validation (uses sanitized name; raises on disallowed type) ---
        validate_file_type(sanitized, content_type)

        # --- Build storage key using sanitized name to prevent path-traversal in key ---
        doc_id = uuid4()
        storage_key = f"project-documents/{project_id}/{doc_id}/{sanitized}"

        # --- Upload to object storage first; DB row only written on success ---
        self._storage.put(storage_key, fileobj, content_type)

        # --- Construct entity with ORIGINAL filename preserved in DB ---
        doc = ProjectDocument(
            id=doc_id,
            project_id=project_id,
            uploader_user_id=uploader_user_id,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            storage_key=storage_key,
            created_at=datetime.now(timezone.utc),
            deleted_at=None,
        )

        try:
            saved = self._repo.save(doc)
            self._db_session.commit()
            return saved
        except Exception:
            # Orphan cleanup: remove the already-uploaded object so storage stays
            # consistent with the DB on commit failure.
            try:
                self._storage.delete(storage_key)
            except Exception:
                _log.warning(
                    "Failed to clean up orphaned storage object %s after DB commit failure",
                    storage_key,
                )
            raise
