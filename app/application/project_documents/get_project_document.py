"""Use case: retrieve a project document and open its storage stream."""

from __future__ import annotations

from typing import BinaryIO
from uuid import UUID

from app.application.project_documents.ports import (
    IDocumentStorage,
    IProjectDocumentRepository,
)
from app.domain.exceptions.project_document_exceptions import ProjectDocumentNotFoundError
from app.domain.project_document import ProjectDocument


class GetProjectDocumentUseCase:
    """Look up a document and open a download stream from object storage."""

    def __init__(
        self,
        repo: IProjectDocumentRepository,
        storage: IDocumentStorage,
    ) -> None:
        self._repo = repo
        self._storage = storage

    def execute(self, doc_id: UUID, expected_project_id: UUID) -> tuple[ProjectDocument, BinaryIO, int]:
        """Return the document entity, a readable binary stream, and its byte length.

        The project ownership check is performed BEFORE opening the storage stream
        so that cross-project probing cannot exhaust the backend→storage connection pool.

        Args:
            doc_id: UUID of the document to retrieve.
            expected_project_id: Project UUID from the URL; must match the document's
                project_id. Raises ProjectDocumentNotFoundError on mismatch so that
                existence of the document is not leaked to callers of other projects.

        Returns:
            A 3-tuple of (ProjectDocument, file-like stream, content_length_bytes).

        Raises:
            ProjectDocumentNotFoundError: Document does not exist, has been soft-deleted,
                or does not belong to expected_project_id.
        """
        doc = self._repo.find_by_id(doc_id)
        if doc is None or doc.deleted_at is not None:
            raise ProjectDocumentNotFoundError(f"Document {doc_id} not found")

        # Cross-project guard: enforce before opening the storage stream so a
        # member of project B who guesses a project-A document UUID cannot exhaust
        # backend→storage TCP connections via repeated probing.
        if doc.project_id != expected_project_id:
            raise ProjectDocumentNotFoundError(f"Document {doc_id} not found")

        stream, length = self._storage.get_stream(doc.storage_key)
        return doc, stream, length
