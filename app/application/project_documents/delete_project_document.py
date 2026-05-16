"""Use case: soft-delete a project document."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.application.project_documents.exceptions import DocumentPermissionDeniedError
from app.application.project_documents.ports import (
    IProjectDocumentRepository,
    ITransactionalSession,
)
from app.domain.exceptions.project_document_exceptions import ProjectDocumentNotFoundError


class DeleteProjectDocumentUseCase:
    """Soft-delete a project document after verifying ownership and permissions.

    The underlying storage object is NOT removed — soft-delete preserves the
    MinIO object for audit trails and potential recovery.
    """

    def __init__(
        self,
        repo: IProjectDocumentRepository,
        db_session: ITransactionalSession,
    ) -> None:
        self._repo = repo
        self._db_session = db_session

    def execute(
        self,
        doc_id: UUID,
        requester_user_id: UUID,
        project: object,
        is_admin: bool = False,
    ) -> None:
        """Soft-delete a document if the requester has permission.

        Args:
            doc_id: UUID of the document to delete.
            requester_user_id: UUID of the authenticated user requesting deletion.
            project: Project domain entity — must expose `.id` and `.owner_id`.
            is_admin: True if the requester holds a company-admin role.

        Raises:
            ProjectDocumentNotFoundError: Document does not exist, is already
                soft-deleted, or belongs to a different project (cross-project
                guard — existence is not leaked to the caller).
            DocumentPermissionDeniedError: Requester is neither the uploader,
                the project owner, nor an admin.
        """
        doc = self._repo.find_by_id(doc_id)

        # Treat missing and already-deleted identically to avoid enumeration.
        if doc is None or doc.deleted_at is not None:
            raise ProjectDocumentNotFoundError(f"Document {doc_id} not found")

        # Cross-project guard: silently map to NotFound so callers cannot probe
        # document existence across projects via DELETE on an unrelated project URL.
        if doc.project_id != project.id:  # type: ignore[attr-defined]
            raise ProjectDocumentNotFoundError(f"Document {doc_id} not found")

        # Permission: admin, project owner, or the original uploader may delete.
        allowed = (
            is_admin
            or doc.uploader_user_id == requester_user_id
            or project.owner_id == requester_user_id  # type: ignore[attr-defined]
        )
        if not allowed:
            raise DocumentPermissionDeniedError(
                f"User {requester_user_id} is not permitted to delete document {doc_id}"
            )

        self._repo.soft_delete(doc_id, datetime.now(timezone.utc))
        self._db_session.commit()
