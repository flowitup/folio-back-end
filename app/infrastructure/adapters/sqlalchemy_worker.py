"""SQLAlchemy implementation of worker repository."""

from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.labor.ports import IWorkerRepository
from app.domain.entities.worker import Worker
from app.infrastructure.database.models import WorkerModel


class SQLAlchemyWorkerRepository(IWorkerRepository):
    """SQLAlchemy adapter for worker persistence."""

    def __init__(self, session: Session):
        self._session = session

    def create(self, worker: Worker) -> Worker:
        model = WorkerModel(
            id=worker.id,
            project_id=worker.project_id,
            name=worker.name,
            phone=worker.phone,
            daily_rate=worker.daily_rate,
            is_active=worker.is_active,
            created_at=worker.created_at,
        )
        self._session.add(model)
        self._session.flush()
        return self._to_entity(model)

    def find_by_id(self, worker_id: UUID) -> Optional[Worker]:
        model = self._session.query(WorkerModel).filter_by(id=worker_id).first()
        return self._to_entity(model) if model else None

    def list_by_project(self, project_id: UUID, active_only: bool = True) -> List[Worker]:
        query = self._session.query(WorkerModel).filter_by(project_id=project_id)
        if active_only:
            query = query.filter_by(is_active=True)
        models = query.order_by(WorkerModel.name).all()
        return [self._to_entity(m) for m in models]

    def update(self, worker: Worker) -> Worker:
        model = self._session.query(WorkerModel).filter_by(id=worker.id).first()
        if model:
            model.name = worker.name
            model.phone = worker.phone
            model.daily_rate = worker.daily_rate
            model.is_active = worker.is_active
            model.updated_at = worker.updated_at
            self._session.flush()
            return self._to_entity(model)
        return worker

    def soft_delete(self, worker_id: UUID) -> bool:
        model = self._session.query(WorkerModel).filter_by(id=worker_id).first()
        if model:
            model.is_active = False
            self._session.flush()
            return True
        return False

    def _to_entity(self, model: WorkerModel) -> Worker:
        return Worker(
            id=model.id,
            project_id=model.project_id,
            name=model.name,
            phone=model.phone,
            daily_rate=Decimal(str(model.daily_rate)),
            is_active=model.is_active,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
