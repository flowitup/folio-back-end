"""Use case: generate a presigned PUT URL for direct-to-S3 browser upload."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from app.application.project_documents.exceptions import (
    DocumentFileTooLargeError,
    EmptyFileError,
    UnsupportedDocumentTypeError,
)
from app.application.project_documents.ports import IDocumentStorage, IFilenameSanitizer
from app.application.project_documents.upload_project_document import (
    MAX_SIZE_BYTES,
    validate_file_type,
)


@dataclass(frozen=True)
class PresignResult:
    """Returned to the route layer — contains everything the browser needs."""

    presigned_url: str
    storage_key: str
    doc_id: str


class PresignProjectDocumentUploadUseCase:
    """Validate file metadata then return a presigned PUT URL.

    No DB row is created here — that happens in the confirm step after the
    browser has uploaded the file directly to S3/MinIO.
    """

    def __init__(
        self,
        storage: IDocumentStorage,
        filename_sanitizer: IFilenameSanitizer,
    ) -> None:
        self._storage = storage
        self._sanitizer = filename_sanitizer

    def execute(
        self,
        *,
        project_id: UUID,
        filename: str,
        content_type: str,
        size_bytes: int,
    ) -> PresignResult:
        # --- Size validation (same rules as multipart upload) ---
        if size_bytes <= 0:
            raise EmptyFileError("File has no content (size <= 0 bytes)")
        if size_bytes > MAX_SIZE_BYTES:
            raise DocumentFileTooLargeError(f"File size {size_bytes} bytes exceeds maximum of {MAX_SIZE_BYTES} bytes")

        # --- Filename sanitation ---
        sanitized = self._sanitizer.sanitize(filename)
        if not sanitized:
            raise UnsupportedDocumentTypeError("Invalid filename after sanitation — no safe characters remain")

        # --- Type validation (extension + MIME) ---
        validate_file_type(sanitized, content_type)

        # --- Build storage key ---
        doc_id = uuid4()
        storage_key = f"project-documents/{project_id}/{doc_id}/{sanitized}"

        # --- Generate presigned URL ---
        presigned_url = self._storage.generate_presigned_put_url(
            key=storage_key,
            content_type=content_type,
        )

        return PresignResult(
            presigned_url=presigned_url,
            storage_key=storage_key,
            doc_id=str(doc_id),
        )
