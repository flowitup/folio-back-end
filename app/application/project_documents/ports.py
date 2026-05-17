"""Ports (interfaces) for the project documents application layer."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, BinaryIO, Optional
from uuid import UUID

from typing import Protocol, runtime_checkable

from app.domain.project_document import ProjectDocument

if TYPE_CHECKING:
    from app.application.project_documents.dtos import ListFiltersDTO, ListResultDTO


@runtime_checkable
class IDocumentStorage(Protocol):
    """Port for binary file storage (S3 / MinIO / local FS)."""

    def put(self, key: str, fileobj: BinaryIO, content_type: str) -> None:
        """Upload a file. `key` is the storage object path."""
        ...

    def get_stream(self, key: str) -> tuple[BinaryIO, int]:
        """Open a download stream. Returns (file-like, content_length_bytes)."""
        ...

    def delete(self, key: str) -> None:
        """Remove an object. Idempotent — no-op if key does not exist."""
        ...


@runtime_checkable
class IProjectDocumentRepository(Protocol):
    """Port defining the project document persistence contract."""

    def save(self, doc: ProjectDocument) -> ProjectDocument:
        """Persist a new document record and return the saved entity."""
        ...

    def find_by_id(self, doc_id: UUID) -> Optional[ProjectDocument]:
        """Return the document or None if not found."""
        ...

    def list_for_project(self, project_id: UUID, filters: "ListFiltersDTO") -> "ListResultDTO":
        """Return a paginated, filtered list of documents for a project."""
        ...

    def soft_delete(self, doc_id: UUID, now: datetime) -> None:
        """Mark the document as deleted by setting deleted_at = now."""
        ...

    def find_soft_deleted_before(self, cutoff: datetime, limit: int = 1000) -> list[ProjectDocument]:
        """Return soft-deleted documents whose `deleted_at < cutoff`.

        Ordered by `deleted_at ASC` so the oldest are purged first. The `limit`
        caps a single batch so the janitor can run incrementally on huge sets
        without exhausting memory.
        """
        ...

    def hard_delete(self, doc_id: UUID) -> None:
        """Permanently remove the document row. Used by the retention janitor."""
        ...


@runtime_checkable
class ITransactionalSession(Protocol):
    """Minimal port for a DB session that can be committed."""

    def commit(self) -> None:
        """Flush pending changes and commit the current transaction."""
        ...


@runtime_checkable
class IFilenameSanitizer(Protocol):
    """Port for sanitizing user-supplied filenames before they hit the filesystem.

    Implementations strip path traversal, NUL bytes, control chars, etc.,
    returning a safe ASCII filename. May return an empty string if no safe
    characters remain — callers must check.
    """

    def sanitize(self, filename: str) -> str:
        """Return a safe filename, or an empty string if nothing safe remains."""
        ...
