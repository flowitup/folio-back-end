"""Use case: upload a self-contained HTML analysis report to a project."""

from __future__ import annotations

import logging
import os
from io import BytesIO
from uuid import uuid4

from app.application.invoice.ports import IAttachmentStorage
from app.application.project_analyses.dtos import AnalysisOutput, CreateAnalysisInput
from app.application.project_analyses.exceptions import (
    AnalysisTooLargeError,
    InvalidAnalysisFileError,
    NotProjectMemberError,
)
from app.application.project_analyses.ports import (
    ProjectAnalysisRepositoryPort,
    ProjectMembershipReaderPort,
    TransactionalSessionPort,
)
from app.domain.entities.project_analysis import ProjectAnalysis, validate_metadata

_log = logging.getLogger(__name__)

# 2 MB — LearnFlow-style self-contained HTML reports run 50-200 KB; this cap
# is generous while keeping the blob:-URL render snappy on the FE.
MAX_SIZE_BYTES = 2 * 1024 * 1024

ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".html", ".htm"})
ALLOWED_MIME_TYPES: frozenset[str] = frozenset({"text/html", "application/xhtml+xml"})


def _validate_extension_and_mimetype(filename: str, content_type: str) -> None:
    """Raise InvalidAnalysisFileError if extension or mimetype are not HTML.

    ``application/octet-stream`` is accepted alongside the allowlist — some
    browsers/proxies report this generic type for local files regardless of
    extension (same tolerance as project_documents' upload validation).
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise InvalidAnalysisFileError(f"File extension '{ext}' is not allowed — expected .html or .htm")
    if content_type not in ALLOWED_MIME_TYPES and content_type != "application/octet-stream":
        raise InvalidAnalysisFileError(f"Content type '{content_type}' is not allowed for an HTML report")


class CreateProjectAnalysisUseCase:
    """Validate, store, and record metadata for an uploaded HTML analysis report.

    Authorization: the acting user (the uploader) must be a member of the
    project. The same endpoint this backs is agent-callable (multipart POST),
    so ``uploader_user_id`` doubles as the acting user — there is no separate
    "upload on behalf of" concept.
    """

    def __init__(
        self,
        repo: ProjectAnalysisRepositoryPort,
        storage: IAttachmentStorage,
        membership_reader: ProjectMembershipReaderPort,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._membership = membership_reader
        self._db = db_session

    def execute(self, data: CreateAnalysisInput) -> AnalysisOutput:
        """Validate the upload, store the body, persist metadata; return the DTO.

        Raises:
            NotProjectMemberError: uploader is not a member of the project.
            AnalysisTooLargeError: file exceeds MAX_SIZE_BYTES.
            InvalidAnalysisFileError: extension/mimetype not HTML, empty file,
                or body is not valid UTF-8 text.
            ValueError: title/summary/source_url/tags fail domain validation.
        """
        if not self._membership.is_member(data.uploader_user_id, data.project_id):
            raise NotProjectMemberError(f"User {data.uploader_user_id} is not a member of project {data.project_id}.")

        # --- Fail fast on bad metadata BEFORE any storage I/O (no orphan objects) ---
        validate_metadata(
            title=data.title,
            summary=data.summary,
            source_url=data.source_url,
            tags=data.tags,
        )

        # --- Size + type validation ---
        if data.size_bytes <= 0:
            raise InvalidAnalysisFileError("Uploaded file has no content (size <= 0 bytes)")
        if data.size_bytes > MAX_SIZE_BYTES:
            raise AnalysisTooLargeError(f"File size {data.size_bytes} bytes exceeds maximum of {MAX_SIZE_BYTES} bytes")

        _validate_extension_and_mimetype(data.filename, data.content_type)

        # --- Read the full body (bounded by the size cap above) to validate UTF-8 ---
        raw = data.fileobj.read()
        if not raw:
            raise InvalidAnalysisFileError("Uploaded file has no content (size <= 0 bytes)")
        if len(raw) > MAX_SIZE_BYTES:
            # Defense-in-depth: the declared size_bytes is client-reported and
            # may lie — trust the actual bytes read for the hard cap.
            raise AnalysisTooLargeError(f"File body ({len(raw)} bytes) exceeds maximum of {MAX_SIZE_BYTES} bytes")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidAnalysisFileError("File body is not valid UTF-8 text") from exc

        analysis_id = uuid4()
        storage_key = f"project-analyses/{data.project_id}/{analysis_id}.html"

        # --- Storage write before DB insert; best-effort cleanup on DB failure ---
        self._storage.put(storage_key, BytesIO(raw), "text/html; charset=utf-8")

        analysis = ProjectAnalysis.create(
            id=analysis_id,
            project_id=data.project_id,
            uploader_user_id=data.uploader_user_id,
            title=data.title,
            summary=data.summary,
            source_url=data.source_url,
            storage_key=storage_key,
            size_bytes=len(raw),
            tags=data.tags,
        )

        try:
            saved = self._repo.add(analysis)
            self._db.commit()
        except Exception:
            try:
                self._storage.delete(storage_key)
            except Exception:
                _log.warning(
                    "Failed to clean up orphaned storage object %s after DB commit failure",
                    storage_key,
                )
            raise

        return AnalysisOutput.from_entity(saved)
