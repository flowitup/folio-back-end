"""SQLAlchemy adapter implementing ProjectStorageKeyReaderPort.

Reads every object-store key that rows of one project point at, so project deletion
can remove the files the FK cascades leave behind. Soft-deleted documents, photos and
analyses are included: their rows die with the project too, so the purge janitor would
never see them again.
"""

from __future__ import annotations

from typing import List
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.projects.ports import ProjectStorageKeyReaderPort
from app.infrastructure.database.models import (
    ChiffrageArticleModel,
    ChiffragePosteModel,
    InvoiceAttachmentModel,
    InvoiceModel,
    ProjectAnalysisModel,
    ProjectDocumentModel,
    ProjectPhotoRow,
)


class SqlAlchemyProjectStorageKeyReader(ProjectStorageKeyReaderPort):
    def __init__(self, session: Session) -> None:
        self._session = session

    def storage_keys_for_project(self, project_id: UUID) -> List[str]:
        s = self._session
        keys: List[str] = []
        keys += [k for (k,) in s.query(ProjectDocumentModel.storage_key).filter_by(project_id=project_id)]
        for original, thumb in s.query(ProjectPhotoRow.storage_key, ProjectPhotoRow.thumbnail_storage_key).filter_by(
            project_id=project_id
        ):
            keys += [original, thumb]
        keys += [
            k
            for (k,) in s.query(InvoiceAttachmentModel.storage_key)
            .join(InvoiceModel, InvoiceModel.id == InvoiceAttachmentModel.invoice_id)
            .filter(InvoiceModel.project_id == project_id)
        ]
        keys += [k for (k,) in s.query(ProjectAnalysisModel.storage_key).filter_by(project_id=project_id)]
        keys += [
            k
            for (k,) in s.query(ChiffrageArticleModel.image_storage_key)
            .join(ChiffragePosteModel, ChiffragePosteModel.id == ChiffrageArticleModel.poste_id)
            .filter(ChiffragePosteModel.project_id == project_id)
        ]
        # Keep order; drop blanks (an article without an image) and duplicates.
        return list(dict.fromkeys(k for k in keys if k))
