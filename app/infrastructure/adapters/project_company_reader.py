"""ProjectCompanyReader — answers IProjectCompanyReader from the projects table."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.database.models.project import ProjectModel


class ProjectCompanyReader:
    """The owning company of a project, or None when it does not exist."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def project_company_id(self, project_id: UUID) -> Optional[UUID]:
        value = self._session.execute(
            select(ProjectModel.company_id).where(ProjectModel.id == project_id)
        ).scalar_one_or_none()
        return value  # type: ignore[no-any-return]
