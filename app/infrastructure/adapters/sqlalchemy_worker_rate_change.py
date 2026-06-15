"""SQLAlchemy implementation of the worker rate-change repository."""

from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.labor.ports import IWorkerRateChangeRepository
from app.domain.entities.worker_rate_change import WorkerRateChange
from app.infrastructure.database.models.worker_rate_change import WorkerRateChangeModel


class SQLAlchemyWorkerRateChangeRepository(IWorkerRateChangeRepository):
    """SQLAlchemy adapter for worker rate-change persistence.

    ``upsert`` always commits so the row is visible to subsequent queries
    inside the same request (same session / unit-of-work).  Without an
    explicit commit the row would be rolled back at request end — same
    class of bug as the labor-entry delete issue documented in PR #29.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # IWorkerRateChangeRepository implementation
    # ------------------------------------------------------------------

    def upsert(self, rc: WorkerRateChange) -> WorkerRateChange:
        """Insert or update the rate change keyed by (worker_id, effective_date)."""
        existing = (
            self._session.query(WorkerRateChangeModel)
            .filter_by(worker_id=rc.worker_id, effective_date=rc.effective_date)
            .first()
        )
        if existing is not None:
            existing.daily_rate = rc.daily_rate
            self._session.commit()
            return self._to_entity(existing)

        model = WorkerRateChangeModel(
            id=rc.id,
            worker_id=rc.worker_id,
            effective_date=rc.effective_date,
            daily_rate=rc.daily_rate,
            created_at=rc.created_at,
        )
        self._session.add(model)
        self._session.commit()
        return self._to_entity(model)

    def list_by_worker(self, worker_id: UUID) -> List[WorkerRateChange]:
        """Return all rate changes for one worker, effective_date DESC."""
        models = (
            self._session.query(WorkerRateChangeModel)
            .filter_by(worker_id=worker_id)
            .order_by(WorkerRateChangeModel.effective_date.desc())
            .all()
        )
        return [self._to_entity(m) for m in models]

    def list_by_workers(self, worker_ids: List[UUID]) -> Dict[UUID, List[WorkerRateChange]]:
        """Return rate changes for multiple workers in a single query.

        The result dict only includes workers that have at least one rate change.
        Each list is ordered effective_date DESC (matching list_by_worker).
        """
        if not worker_ids:
            return {}

        models = (
            self._session.query(WorkerRateChangeModel)
            .filter(WorkerRateChangeModel.worker_id.in_(worker_ids))
            .order_by(
                WorkerRateChangeModel.worker_id,
                WorkerRateChangeModel.effective_date.desc(),
            )
            .all()
        )

        result: Dict[UUID, List[WorkerRateChange]] = {}
        for model in models:
            wid = model.worker_id
            if wid not in result:
                result[wid] = []
            result[wid].append(self._to_entity(model))
        return result

    def find_by_id(self, rc_id: UUID) -> Optional[WorkerRateChange]:
        """Return the rate change by primary key, or None."""
        model = self._session.query(WorkerRateChangeModel).filter_by(id=rc_id).first()
        return self._to_entity(model) if model is not None else None

    def delete(self, rc_id: UUID) -> bool:
        """Delete the rate change. Returns True if deleted, False if not found.

        Uses load-then-delete (not bulk delete) so the session unit-of-work
        is cleanly closed — prevents the same stale-row bug class as
        labor-entry bulk delete (PR #29).
        """
        model = self._session.query(WorkerRateChangeModel).filter_by(id=rc_id).first()
        if model is None:
            return False
        self._session.delete(model)
        self._session.commit()
        return True

    # ------------------------------------------------------------------
    # Mapping helper
    # ------------------------------------------------------------------

    @staticmethod
    def _to_entity(model: WorkerRateChangeModel) -> WorkerRateChange:
        return WorkerRateChange(
            id=model.id,
            worker_id=model.worker_id,
            effective_date=model.effective_date,
            daily_rate=Decimal(str(model.daily_rate)),
            created_at=model.created_at,
        )
