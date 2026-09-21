"""SQLAlchemy repository for ``assistant_jobs`` (feature B — invoice fetch).

Constructed from a plain ``sqlalchemy.orm.Session`` — either Flask-SQLAlchemy's
``db.session`` (web process, ``process_fetched_invoice`` RQ job) or a bare
``sessionmaker(bind=create_engine(DATABASE_URL))()`` (the ``ai-browser`` container,
which has no Flask app context — see ``app.infrastructure.browser_worker``). Neither
``AssistantJobModel`` nor this module imports anything Flask-specific, which is what
makes that dual use possible.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.assistant.jobs_repo import ACTIVE_JOB_STATUSES, AssistantJobRecord
from app.infrastructure.database.models.assistant_job import AssistantJobModel


def _to_entity(m: AssistantJobModel) -> AssistantJobRecord:
    return AssistantJobRecord(
        id=m.id,
        type=m.type,
        user_id=m.user_id,
        merchant=m.merchant,
        amount_ttc=Decimal(m.amount_ttc),
        date=m.date,
        project_hint=m.project_hint,
        status=m.status,
        attempts=m.attempts,
        run_after=m.run_after,
        result=dict(m.result) if m.result else None,
        pdf_storage_key=m.pdf_storage_key,
        status_message_id=m.status_message_id,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


class SqlAlchemyAssistantJobRepository:
    """Implements ``AssistantJobRepositoryPort``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        *,
        user_id: UUID,
        merchant: str,
        amount_ttc: Decimal,
        date: Any,
        project_hint: Optional[str],
        status_message_id: Optional[UUID] = None,
    ) -> AssistantJobRecord:
        now = datetime.now(timezone.utc)
        model = AssistantJobModel(
            id=uuid4(),
            type="fetch_invoice",
            user_id=user_id,
            merchant=merchant,
            amount_ttc=amount_ttc,
            date=date,
            project_hint=project_hint,
            status="queued",
            attempts=0,
            run_after=now,
            result=None,
            pdf_storage_key=None,
            status_message_id=status_message_id,
            created_at=now,
            updated_at=now,
        )
        self._session.add(model)
        self._session.commit()
        return _to_entity(model)

    def find_by_id(self, job_id: UUID) -> Optional[AssistantJobRecord]:
        model = self._session.get(AssistantJobModel, job_id)
        return _to_entity(model) if model is not None else None

    def find_duplicate(
        self, *, user_id: UUID, merchant: str, amount_ttc: Decimal, date: Any, since: datetime
    ) -> Optional[AssistantJobRecord]:
        model = (
            self._session.execute(
                select(AssistantJobModel)
                .where(
                    AssistantJobModel.user_id == user_id,
                    AssistantJobModel.merchant == merchant,
                    AssistantJobModel.amount_ttc == amount_ttc,
                    AssistantJobModel.date == date,
                    AssistantJobModel.status.in_(ACTIVE_JOB_STATUSES),
                    AssistantJobModel.created_at >= since,
                )
                .order_by(AssistantJobModel.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        return _to_entity(model) if model is not None else None

    def set_status_message(self, job_id: UUID, status_message_id: UUID) -> None:
        model = self._session.get(AssistantJobModel, job_id)
        if model is None:
            return
        model.status_message_id = status_message_id
        model.updated_at = datetime.now(timezone.utc)
        self._session.commit()

    def claim_next(self, now: datetime) -> Optional[AssistantJobRecord]:
        query = (
            select(AssistantJobModel)
            .where(AssistantJobModel.status == "queued", AssistantJobModel.run_after <= now)
            .order_by(AssistantJobModel.run_after.asc())
            .limit(1)
        )
        bind = self._session.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        model = self._session.execute(query).scalars().first()
        if model is None:
            return None
        model.status = "running"
        model.updated_at = datetime.now(timezone.utc)
        self._session.commit()
        return _to_entity(model)

    def update_status(
        self, job_id: UUID, *, status: str, attempts: Optional[int] = None, run_after: Optional[datetime] = None
    ) -> None:
        model = self._session.get(AssistantJobModel, job_id)
        if model is None:
            return
        model.status = status
        if attempts is not None:
            model.attempts = attempts
        if run_after is not None:
            model.run_after = run_after
        model.updated_at = datetime.now(timezone.utc)
        self._session.commit()

    def update_result(
        self,
        job_id: UUID,
        *,
        status: str,
        result: Optional[dict[str, Any]] = None,
        pdf_storage_key: Optional[str] = None,
    ) -> None:
        model = self._session.get(AssistantJobModel, job_id)
        if model is None:
            return
        model.status = status
        if result is not None:
            model.result = dict(result)
        if pdf_storage_key is not None:
            model.pdf_storage_key = pdf_storage_key
        model.updated_at = datetime.now(timezone.utc)
        self._session.commit()

    def list_recent_for_user(self, user_id: UUID, limit: int = 10) -> list[AssistantJobRecord]:
        models = (
            self._session.execute(
                select(AssistantJobModel)
                .where(AssistantJobModel.user_id == user_id)
                .order_by(AssistantJobModel.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [_to_entity(m) for m in models]
