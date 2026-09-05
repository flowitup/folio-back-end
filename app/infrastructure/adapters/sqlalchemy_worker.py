"""SQLAlchemy implementation of worker repository."""

from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.application.labor.ports import IWorkerRepository
from app.domain.entities.worker import Worker
from app.domain.exceptions.labor_exceptions import InvalidWorkerDataError
from app.infrastructure.database.models import WorkerModel

_USER_ALREADY_LINKED = "This account is already linked to another worker on this project"
_USER_ID_INVALID = "user_id does not reference an existing user"


def _integrity_error_to_domain(exc: IntegrityError) -> InvalidWorkerDataError:
    """Map the violated constraint to a message the operator can act on."""
    detail = str(getattr(exc, "orig", exc))
    if "uq_workers_project_user" in detail:
        return InvalidWorkerDataError(_USER_ALREADY_LINKED)
    if "fk_workers_user_id" in detail:
        return InvalidWorkerDataError(_USER_ID_INVALID)
    return InvalidWorkerDataError(f"Worker could not be saved: {detail.splitlines()[0][:200]}")


class SQLAlchemyWorkerRepository(IWorkerRepository):
    """SQLAlchemy adapter for worker persistence."""

    def __init__(self, session: Session):
        self._session = session

    def create(self, worker: Worker) -> Worker:
        model = WorkerModel(
            id=worker.id,
            project_id=worker.project_id,
            person_id=worker.person_id,
            role_id=worker.role_id,
            name=worker.name,
            phone=worker.phone,
            daily_rate=worker.daily_rate,
            is_active=worker.is_active,
            created_at=worker.created_at,
            user_id=worker.user_id,
        )
        self._session.add(model)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise _integrity_error_to_domain(exc)
        return self._to_entity(model)

    def find_by_id(self, worker_id: UUID) -> Optional[Worker]:
        model = self._session.query(WorkerModel).filter_by(id=worker_id).first()
        return self._to_entity(model) if model else None

    def list_by_project(self, project_id: UUID, active_only: bool = True) -> List[Worker]:
        query = (
            self._session.query(WorkerModel)
            .options(joinedload(WorkerModel.role), joinedload(WorkerModel.person))
            .filter_by(project_id=project_id)
        )
        if active_only:
            query = query.filter_by(is_active=True)
        models = query.order_by(WorkerModel.name).all()
        return [self._to_entity(m) for m in models]

    def find_by_project_and_user(self, project_id: UUID, user_id: UUID) -> Optional[Worker]:
        model = self._session.query(WorkerModel).filter_by(project_id=project_id, user_id=user_id).first()
        return self._to_entity(model) if model else None

    def update(self, worker: Worker) -> Worker:
        model = self._session.query(WorkerModel).filter_by(id=worker.id).first()
        if model:
            model.name = worker.name
            model.phone = worker.phone
            model.daily_rate = worker.daily_rate
            model.role_id = worker.role_id
            model.is_active = worker.is_active
            model.user_id = worker.user_id
            model.updated_at = worker.updated_at
            try:
                self._session.commit()
            except IntegrityError as exc:
                self._session.rollback()
                raise _integrity_error_to_domain(exc)
            return self._to_entity(model)
        return worker

    def soft_delete(self, worker_id: UUID) -> bool:
        model = self._session.query(WorkerModel).filter_by(id=worker_id).first()
        if model:
            model.is_active = False
            # Free the (project, user) slot so the same account can be re-added later.
            model.user_id = None
            self._session.commit()
            return True
        return False

    def _to_entity(self, model: WorkerModel) -> Worker:
        # Person FK is nullable during the Phase 1c backfill rollout — guard
        # against unlinked rows. Accessing model.person triggers the lazy
        # join, so we only pay for it once per worker per session.
        person = model.person if model.person_id else None
        # Role FK is nullable — only resolve the relationship when set.
        role = model.role if model.role_id else None
        return Worker(
            id=model.id,
            project_id=model.project_id,
            name=model.name,
            phone=model.phone,
            daily_rate=Decimal(str(model.daily_rate)),
            is_active=model.is_active,
            created_at=model.created_at,
            updated_at=model.updated_at,
            person_id=model.person_id,
            person_name=person.name if person else None,
            person_phone=person.phone if person else None,
            role_id=model.role_id,
            role_name=role.name if role else None,
            role_color=role.color if role else None,
            user_id=model.user_id,
        )
