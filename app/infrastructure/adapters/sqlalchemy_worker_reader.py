"""SQLAlchemy implementation of the invoice BC's read-only worker lookup port.

Structurally satisfies WorkerReaderPort (app.application.invoice.ports) without
depending on labor's IWorkerRepository — keeps the invoice bounded context
decoupled from the labor domain's Worker entity/repository.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.application.invoice.ports import WorkerRef
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.worker import WorkerModel


class SQLAlchemyWorkerReader:
    """Read-only worker lookup, scoped to a single project, for invoice worker links."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_project(self, worker_id: UUID, project_id: UUID) -> Optional[WorkerRef]:
        """Return the worker if it exists AND belongs to project_id, else None.

        display_name mirrors the labor summary precedent (sqlalchemy_labor_entry.py):
        COALESCE(persons.name, workers.name) via an outer join so a worker with no
        linked person still resolves to its own name.
        """
        display_name = func.coalesce(PersonModel.name, WorkerModel.name)
        row = (
            self._session.query(
                WorkerModel.id,
                WorkerModel.project_id,
                display_name.label("display_name"),
            )
            .outerjoin(PersonModel, WorkerModel.person_id == PersonModel.id)
            .filter(WorkerModel.id == worker_id, WorkerModel.project_id == project_id)
            .first()
        )
        if row is None:
            return None
        return WorkerRef(id=row.id, project_id=row.project_id, display_name=row.display_name)
