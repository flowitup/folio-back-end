"""ISoftDeletedFileSource adapters for the storage janitor."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.application.storage_janitor import PurgeCandidate
from app.infrastructure.database.models.project_analysis import ProjectAnalysisModel
from app.infrastructure.database.models.project_document import ProjectDocumentModel
from app.infrastructure.database.models.project_photo import ProjectPhotoRow


class _SoftDeletedSource:
    name = ""
    _model: Any = None

    def __init__(self, session: Session) -> None:
        self._session = session

    def _keys(self, row: Any) -> tuple[str, ...]:
        return (row.storage_key,)

    def find_soft_deleted_before(self, cutoff: datetime, limit: int) -> list[PurgeCandidate]:
        model = self._model
        stmt = (
            select(model)
            .where(model.deleted_at.is_not(None), model.deleted_at < cutoff)
            .order_by(model.deleted_at.asc(), model.id.asc())
            .limit(limit)
        )
        rows = self._session.execute(stmt).scalars().all()
        return [PurgeCandidate(id=row.id, storage_keys=tuple(k for k in self._keys(row) if k)) for row in rows]

    def hard_delete(self, row_id: UUID) -> None:
        self._session.execute(delete(self._model).where(self._model.id == row_id))
        self._session.flush()


class SoftDeletedDocumentSource(_SoftDeletedSource):
    name = "documents"
    _model = ProjectDocumentModel


class SoftDeletedPhotoSource(_SoftDeletedSource):
    """Photos and videos: the original plus its JPEG thumbnail / poster."""

    name = "photos"
    _model = ProjectPhotoRow

    def _keys(self, row: Any) -> tuple[str, ...]:
        return (row.storage_key, row.thumbnail_storage_key)


class SoftDeletedAnalysisSource(_SoftDeletedSource):
    name = "analyses"
    _model = ProjectAnalysisModel


def known_document_keys(session: Session):
    """Callable returning which of the given storage keys a project_documents row (live or deleted) owns."""

    def known(keys) -> set[str]:
        keys = list(keys)
        if not keys:
            return set()
        stmt = select(ProjectDocumentModel.storage_key).where(ProjectDocumentModel.storage_key.in_(keys))
        return set(session.execute(stmt).scalars().all())

    return known
