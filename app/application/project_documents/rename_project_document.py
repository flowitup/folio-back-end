"""Use case: rename a project document (update its display filename)."""

from __future__ import annotations

import os
from uuid import UUID

from app.application.project_documents.exceptions import DocumentPermissionDeniedError
from app.application.project_documents.ports import (
    IProjectDocumentRepository,
    ITransactionalSession,
)
from app.domain.exceptions.project_document_exceptions import ProjectDocumentNotFoundError
from app.domain.project_document import ProjectDocument


class RenameProjectDocumentUseCase:
    """Rename a project document after verifying ownership and permissions.

    Only the display filename is changed — the storage key remains the same.
    The new filename must preserve the original file extension.
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
        new_filename: str,
        requester_user_id: UUID,
        project: object,
        is_admin: bool = False,
    ) -> ProjectDocument:
        doc = self._repo.find_by_id(doc_id)

        if doc is None or doc.deleted_at is not None:
            raise ProjectDocumentNotFoundError(f"Document {doc_id} not found")

        if doc.project_id != project.id:  # type: ignore[attr-defined]
            raise ProjectDocumentNotFoundError(f"Document {doc_id} not found")

        allowed = (
            is_admin
            or doc.uploader_user_id == requester_user_id
            or project.owner_id == requester_user_id  # type: ignore[attr-defined]
        )
        if not allowed:
            raise DocumentPermissionDeniedError(
                f"User {requester_user_id} is not permitted to rename document {doc_id}"
            )

        new_filename = new_filename.strip()
        if not new_filename:
            raise ValueError("Filename cannot be empty")

        original_ext = os.path.splitext(doc.filename)[1].lower()
        new_ext = os.path.splitext(new_filename)[1].lower()
        if new_ext != original_ext:
            raise ValueError(f"File extension must remain {original_ext}")

        self._repo.update_filename(doc_id, new_filename)
        self._db_session.commit()

        updated = self._repo.find_by_id(doc_id)
        assert updated is not None
        return updated
